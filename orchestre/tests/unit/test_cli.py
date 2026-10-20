import io
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from orchestre.cli.main import load_env_file, main
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from tests.unit.test_binance_adapter import MARKET, StubClient

EXAMPLE = str(Path(__file__).parents[2] / "config" / "settings.example.toml")
KEYS = {"BINANCE_TESTNET_API_KEY": "kk-test", "BINANCE_TESTNET_API_SECRET": "ss-test"}


def run(argv, environ=None, factory=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ or {}, out=out, err=err, client_factory=factory)
    return code, out.getvalue(), err.getvalue()


def write_cfg(tmp_path, text):
    p = tmp_path / "s.toml"
    p.write_text(text)
    return str(p)


def test_check_config_prints_environment_banner():
    code, out, _ = run(["check-config", "--config", EXAMPLE])
    assert code == 0 and "TESTNET" in out and "dry_run" in out


def test_check_config_refuses_live_without_flag(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    code, _, err = run(["check-config", "--config", cfg])
    assert code == 2 and "--live" in err


def test_check_config_live_with_both_confirmations_warns(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    code, out, _ = run(["check-config", "--config", cfg, "--live"])
    assert code == 0 and "LIVE" in out and "ARGENT RÉEL" in out


def test_check_config_reports_invalid_file(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "moon"\n')
    code, _, err = run(["check-config", "--config", cfg])
    assert code == 2 and "env invalide" in err


def test_check_keys_testnet_connectivity_without_leaking_secrets():
    stub = StubClient()
    seen = {}

    def factory(env, key, secret, live_confirmed=False):
        seen.update(env=env, live=live_confirmed)
        return stub

    code, out, err = run(["check-keys", "--config", EXAMPLE], KEYS, factory)
    assert code == 0 and "BTCUSDT" in out
    assert "kk-test" not in out + err and "ss-test" not in out + err
    assert seen["live"] is False


def test_check_keys_missing_credentials():
    code, _, err = run(["check-keys", "--config", EXAMPLE], {}, lambda *a, **k: StubClient())
    assert code == 2 and "BINANCE_TESTNET_API_KEY" in err


def test_check_keys_network_error_is_masked():
    class Boom(StubClient):
        def load_markets(self):
            raise RuntimeError("401 apiKey=kk-test secret=ss-test")

    code, out, err = run(["check-keys", "--config", EXAMPLE], KEYS, lambda *a, **k: Boom())
    assert code == 4 and "kk-test" not in out + err and "ss-test" not in out + err


class LiveStub(StubClient):
    def __init__(self, restrictions):
        super().__init__()
        self.restrictions = restrictions

    def sapiGetAccountApiRestrictions(self):
        return self.restrictions


def live_args(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    env = {"BINANCE_LIVE_API_KEY": "lk", "BINANCE_LIVE_API_SECRET": "ls"}
    return ["check-keys", "--config", cfg, "--live"], env


GOOD = {
    "ipRestrict": True, "enableWithdrawals": False, "enableInternalTransfer": False,
    "permitsUniversalTransfer": False, "enableMargin": False, "enableFutures": False,
    "enableSpotAndMarginTrading": True,
}


def test_check_keys_live_accepts_safe_key(tmp_path):
    argv, env = live_args(tmp_path)
    code, out, _ = run(argv, env, lambda *a, **k: LiveStub(GOOD))
    assert code == 0 and "restrictions conformes" in out


def test_check_keys_live_refuses_dangerous_key(tmp_path):
    argv, env = live_args(tmp_path)
    bad = {**GOOD, "enableWithdrawals": True, "ipRestrict": False}
    code, _, err = run(argv, env, lambda *a, **k: LiveStub(bad))
    assert code == 3 and "retraits" in err and "restriction IP" in err


def test_live_client_receives_confirmation_only_with_flag(tmp_path):
    argv, env = live_args(tmp_path)
    flags = []

    def factory(e, k, s, live_confirmed=False):
        flags.append(live_confirmed)
        return LiveStub(GOOD)

    run(argv, env, factory)
    assert flags == [True]


def test_kill_creates_stop_file(tmp_path):
    stop = tmp_path / "var" / "STOP"
    code, out, _ = run(["kill", "--stop-file", str(stop)])
    assert code == 0 and stop.exists() and "STOP" in out


def test_status_without_database(tmp_path):
    code, out, _ = run(["status", "--db", str(tmp_path / "none.db")])
    assert code == 0 and "aucune base" in out


def test_status_shows_ledger(tmp_path):
    db = tmp_path / "o.db"
    lg = Ledger(D("500"))
    lg.allocate("grid", D("200"))
    lg.commit("grid", D("59.56"))
    Store(Database(db)).save_all(lg, [], {}, datetime(2026, 10, 20, tzinfo=timezone.utc))
    code, out, _ = run(["status", "--db", str(db)])
    assert code == 0 and "grid" in out and "59.56" in out and "300" in out


def test_env_file_does_not_override_real_environment(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nA=1\nB='deux'\n\nC = trois\nBAD LINE\n")
    env = {"A": "reel"}
    load_env_file(f, env)
    assert env == {"A": "reel", "B": "deux", "C": "trois"}


def test_missing_env_file_is_ignored(tmp_path):
    env = {}
    load_env_file(tmp_path / "absent", env)
    assert env == {}
