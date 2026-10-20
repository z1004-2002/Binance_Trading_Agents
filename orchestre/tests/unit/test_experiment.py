import io
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestre.cli.main import main
from orchestre.experiment.protocol import (
    Protocol, ProtocolError, evaluate, file_sha256, load_protocol, start_experiment, verify_experiment,
)
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)
CONFIG = Path(__file__).parents[2] / "config" / "experiments"
PROTOCOL = """name = "Test"
level = "L0"
min_days = 5
[success]
max_drawdown = 0.15
drawdown_below_reference = true
sortino_at_least_reference = true
max_critical_incidents = 0
max_fees_pct_of_capital = 0.02
[stop]
drawdown = 0.15
critical_incidents = 2
"""


def store_with_series(team, ref, fees="0.5", capital="500"):
    store = Store(Database(":memory:"))
    store.save_all(Ledger(D(capital)), [], {}, T0)
    for day, (t, r) in enumerate(zip(team, ref)):
        store.log_equity(T0 + timedelta(days=day), "TEAM", t)
        store.log_equity(T0 + timedelta(days=day), "REF_BTCUSDT", r)
    store.log_order(T0, "dca", "BTCUSDT", "BUY", "0.001", "60000", fees, "FILLED", "c1", "")
    return store


def proto(tmp_path, text=PROTOCOL):
    f = tmp_path / "p.toml"
    f.write_text(text)
    return f


def test_the_shipped_protocols_load_and_carry_the_specified_criteria():
    e1, e2 = load_protocol(CONFIG / "e1-paper.toml"), load_protocol(CONFIG / "e2-pilot.toml")
    assert e1.level == "L0" and e1.min_days == 30 and e2.level == "L2" and e2.min_days == 90
    assert e2.max_drawdown == 0.15 and e2.max_fees_pct == 0.02 and e2.max_incidents == 0


def test_invalid_protocols_are_refused(tmp_path):
    with pytest.raises(ProtocolError):
        load_protocol(proto(tmp_path, 'name = "x"\n'))
    with pytest.raises(ProtocolError):
        load_protocol(proto(tmp_path, PROTOCOL.replace("max_drawdown = 0.15", "max_drawdown = 3")))
    with pytest.raises(ProtocolError):
        load_protocol(tmp_path / "absent.toml")


def test_starting_records_the_fingerprint_and_cannot_be_restarted(tmp_path):
    store = store_with_series([100] * 3, [100] * 3)
    f = proto(tmp_path)
    started = start_experiment(store, f, T0)
    assert started["sha256"] == file_sha256(f) and store.load_state("experiment")["name"] == "Test"
    with pytest.raises(ProtocolError, match="déjà démarrée"):
        start_experiment(store, f, T0)


def test_modifying_the_protocol_after_the_start_invalidates_the_experiment(tmp_path):
    store = store_with_series([100] * 3, [100] * 3)
    f = proto(tmp_path)
    start_experiment(store, f, T0)
    assert verify_experiment(store, f) is True
    f.write_text(PROTOCOL.replace("max_drawdown = 0.15", "max_drawdown = 0.30"))      # on assouplit un critère en cours de route
    assert verify_experiment(store, f) is False


def good_series(days=8):
    team = [1000 + 6 * d + (-1) ** d * 4 for d in range(days)]
    ref = [1000 - 30 * d + (-1) ** d * 50 for d in range(days)]
    return team, ref


def test_evaluation_marks_each_criterion(tmp_path):
    team, ref = good_series()
    result = evaluate(load_protocol(proto(tmp_path)), store_with_series(team, ref), T0 + timedelta(days=8))
    by = {c.name: c.status for c in result.criteria}
    assert by["durée minimale"] == "OK" and by["drawdown maximal"] == "OK" and by["drawdown < référence"] == "OK"
    assert by["incidents critiques"] == "OK" and by["frais"] == "OK" and result.stop_reason is None


def test_too_early_means_unknown_not_success(tmp_path):
    team, ref = good_series(3)
    result = evaluate(load_protocol(proto(tmp_path)), store_with_series(team, ref), T0 + timedelta(days=3))
    assert {c.name: c.status for c in result.criteria}["durée minimale"] == "TROP TÔT" and not result.passed


def test_a_drawdown_beyond_the_stop_rule_triggers_the_return_to_l0(tmp_path):
    team = [1000, 1010, 900, 850, 860, 870, 880, 890]            # 1010 -> 850 : -15,8 %
    result = evaluate(load_protocol(proto(tmp_path)), store_with_series(team, [1000] * 8), T0 + timedelta(days=8))
    assert result.stop_reason and "drawdown" in result.stop_reason and not result.passed


def test_fees_above_the_limit_fail(tmp_path):
    team, ref = good_series()
    result = evaluate(load_protocol(proto(tmp_path)), store_with_series(team, ref, fees="20"), T0 + timedelta(days=8))
    assert {c.name: c.status for c in result.criteria}["frais"] == "NON"


def test_duplicate_fills_count_as_critical_incidents(tmp_path):
    team, ref = good_series()
    store = store_with_series(team, ref)
    store.log_order(T0, "dca", "BTCUSDT", "BUY", "0.001", "60000", "0.01", "FILLED", "c1", "doublon")      # même identifiant exécuté deux fois
    result = evaluate(load_protocol(proto(tmp_path)), store, T0 + timedelta(days=8))
    assert {c.name: c.status for c in result.criteria}["incidents critiques"] == "NON" and result.incidents == 1


def test_cli_start_and_status(tmp_path):
    db = tmp_path / "e.db"
    store = store_with_series(*good_series())
    store.db.close()
    import shutil
    # on part d'une base vide sur disque
    s2 = Store(Database(db))
    s2.save_all(Ledger(D("500")), [], {}, T0)
    for day, (t, r) in enumerate(zip(*good_series())):
        s2.log_equity(T0 + timedelta(days=day), "TEAM", t)
        s2.log_equity(T0 + timedelta(days=day), "REF_BTCUSDT", r)
    f = proto(tmp_path)

    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        return main(list(argv), environ={}, out=out, err=err), out.getvalue(), err.getvalue()

    code, out, _ = run("experiment", "start", "--protocol", str(f), "--db", str(db))
    assert code == 0 and "empreinte" in out.lower()
    assert run("experiment", "start", "--protocol", str(f), "--db", str(db))[0] == 2
    code, out, _ = run("experiment", "status", "--protocol", str(f), "--db", str(db))
    assert code in (0, 1) and "drawdown maximal" in out
    f.write_text(PROTOCOL.replace("min_days = 5", "min_days = 1"))
    code, _, err = run("experiment", "status", "--protocol", str(f), "--db", str(db))
    assert code == 3 and "modifié" in err.lower()
