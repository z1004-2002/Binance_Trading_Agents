import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestre.config import ConfigError
from orchestre.launcher import Component, Launcher, LauncherError, plan_components, read_env_names, running_pid, stop_running

# ---------- faux processus ----------

class FakeProc:
    _next_pid = 1000

    def __init__(self, argv, **kw):
        FakeProc._next_pid += 1
        self.pid, self.argv, self.kw = FakeProc._next_pid, argv, kw
        self.code, self.signals = None, []

    def poll(self):
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)
        if sig == signal.SIGINT and not getattr(self, "stubborn", False):
            self.code = 130

    def terminate(self):
        self.signals.append(signal.SIGTERM)
        if not getattr(self, "very_stubborn", False):
            self.code = -15

    def kill(self):
        self.signals.append(signal.SIGKILL)
        self.code = -9

    def wait(self, timeout=None):
        return self.code


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def launcher(tmp_path, components, **kw):
    clock, procs = Clock(), []

    def spawn(argv, **k):
        p = FakeProc(argv, **k)
        p.stubborn = kw.get("stubborn", False)
        procs.append(p)
        return p

    kw.pop("stubborn", None)
    lc = Launcher(components, tmp_path / "logs", tmp_path / "run", spawn=spawn, clock=clock, sleep=clock.sleep, **kw)
    return lc, procs, clock


COMPONENTS = [Component("moteur", ["engine"]), Component("tableau de bord", ["dash"], url="http://127.0.0.1:8765")]


# ---------- supervision ----------

def test_start_spawns_every_component_with_a_log_file_and_writes_the_pid_and_status(tmp_path):
    lc, procs, _ = launcher(tmp_path, COMPONENTS)
    lc.start()
    assert [p.argv for p in procs] == [["engine"], ["dash"]]
    assert (tmp_path / "logs" / "moteur.log").exists() and (tmp_path / "run" / "up.pid").read_text() == str(os.getpid())
    status = json.loads((tmp_path / "run" / "up.json").read_text())
    assert [s["name"] for s in status["components"]] == ["moteur", "tableau de bord"] and status["components"][0]["status"] == "en marche"
    lc.stop()


def test_a_second_launcher_refuses_to_start_while_one_is_alive(tmp_path):
    lc, _, _ = launcher(tmp_path, COMPONENTS)
    lc.start()
    lc2, _, _ = launcher(tmp_path, COMPONENTS)
    with pytest.raises(LauncherError, match="déjà"):
        lc2.start()
    lc.stop()


def test_a_stale_pid_file_is_ignored(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "up.pid").write_text("999999")
    assert running_pid(tmp_path / "run") is None
    lc, procs, _ = launcher(tmp_path, COMPONENTS)
    lc.start()
    assert len(procs) == 2
    lc.stop()


def test_a_crashed_component_is_restarted_with_backoff(tmp_path):
    lc, procs, clock = launcher(tmp_path, COMPONENTS)
    lc.start()
    procs[1].code = 1                                   # le tableau de bord plante
    lc.poll()
    assert lc.states[1].status == "redémarrage prévu" and len(procs) == 2
    clock.t += 1.5
    lc.poll()
    assert len(procs) == 3 and lc.states[1].status == "en marche" and lc.states[1].restarts_count == 1
    lc.stop()


def test_a_clean_exit_is_not_restarted(tmp_path):
    lc, procs, clock = launcher(tmp_path, COMPONENTS)
    lc.start()
    procs[0].code = 0                                   # le moteur s'est arrêté volontairement (kill switch, fin de session)
    clock.t += 100
    lc.poll()
    assert lc.states[0].status == "terminé" and len(procs) == 2


def test_restarts_are_capped_and_the_failure_is_reported(tmp_path):
    lc, procs, clock = launcher(tmp_path, COMPONENTS, max_restarts=3)
    lc.start()
    for _ in range(10):
        for p in procs:
            if p.code is None and p.argv == ["dash"]:
                p.code = 1
        lc.poll()
        clock.t += 100
        lc.poll()
    assert lc.states[1].status == "en échec" and len([p for p in procs if p.argv == ["dash"]]) == 4      # 1 + 3 relances
    assert lc.states[0].status == "en marche"                                                           # l'autre composant n'est pas touché
    lc.stop()


def test_a_component_marked_non_restartable_stays_down(tmp_path):
    lc, procs, clock = launcher(tmp_path, [Component("moteur", ["engine"], restart=False)])
    lc.start()
    procs[0].code = 1
    lc.poll()
    clock.t += 100
    lc.poll()
    assert lc.states[0].status == "en échec" and len(procs) == 1


def test_stop_asks_politely_first_then_insists_then_kills(tmp_path):
    lc, procs, _ = launcher(tmp_path, COMPONENTS, term_timeout=2.0)
    lc.start()
    procs[0].stubborn = True                            # ignore SIGINT
    procs[1].stubborn = True
    procs[1].very_stubborn = True                       # ignore SIGINT et SIGTERM
    lc.stop()
    assert procs[0].signals == [signal.SIGINT, signal.SIGTERM]
    assert procs[1].signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
    assert not (tmp_path / "run" / "up.pid").exists()
    assert all(s.status == "arrêté" for s in lc.states)


def test_a_clean_stop_only_sends_sigint(tmp_path):
    lc, procs, _ = launcher(tmp_path, COMPONENTS)
    lc.start()
    lc.stop()
    assert all(p.signals == [signal.SIGINT] for p in procs)


def test_run_forever_polls_until_asked_to_stop(tmp_path):
    lc, procs, clock = launcher(tmp_path, COMPONENTS)
    lc.start()
    lc.run_forever(interval=1.0, max_loops=3)
    assert clock.t >= 3.0 and all(p.signals for p in procs)             # l'arrêt propre a bien été fait à la fin


# ---------- vrais processus ----------

def test_real_processes_are_started_supervised_and_stopped_gracefully(tmp_path):
    sleeper = [sys.executable, "-c", "import time\ntry:\n    time.sleep(60)\nexcept KeyboardInterrupt:\n    raise SystemExit(0)"]
    lc = Launcher([Component("a", sleeper), Component("b", sleeper)], tmp_path / "logs", tmp_path / "run")
    lc.start()
    time.sleep(0.4)
    assert all(s.proc.poll() is None for s in lc.states)
    started = time.time()
    lc.stop(term_timeout=5.0)
    assert time.time() - started < 4 and all(s.proc.poll() is not None for s in lc.states)
    assert running_pid(tmp_path / "run") is None


def test_stop_running_signals_the_pid_of_a_running_launcher(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    (run / "up.pid").write_text(str(child.pid))
    assert running_pid(run) == child.pid
    assert stop_running(run, timeout=5.0) is True
    child.wait(timeout=5)
    assert stop_running(run, timeout=1.0) is False                      # plus rien à arrêter


# ---------- plan : quels composants, avec quelles conditions ----------

def settings_file(tmp_path, env="testnet", strategies=True):
    body = f'env = "{env}"\nmax_total_capital_quote = "500"\n'
    if strategies:
        body += '[strategies.dca]\nenabled = true\nsymbol = "BTCUSDT"\namount_quote = "20"\ninterval_days = 7\ncumulative_cap = "100"\n'
    f = tmp_path / f"s-{env}-{int(strategies)}.toml"
    f.write_text(body)
    return f


def args(tmp_path, **kw):
    base = dict(config=str(settings_file(tmp_path)), db=None, stop_file=str(tmp_path / "STOP"), mode="paper", port=8765,
                interval=30.0, env_file=".env", no_dashboard=False, no_assistant=False, no_analysis=False, ticks=None,
                paper_cash=None, python=sys.executable)
    base.update(kw)
    return type("A", (), base)()


FREE = lambda port: True  # noqa: E731


def test_paper_plan_needs_no_exchange_keys_and_includes_the_available_extras(tmp_path):
    present = {"DASHBOARD_PASSWORD": 12, "GROQ_API_KEY": 30, "GROQ_MODEL": 8}
    plan = plan_components(args(tmp_path), present, port_free=FREE)
    names = [c.name for c in plan.components]
    assert names == ["moteur", "tableau de bord", "assistant"] and plan.warnings == []
    engine = plan.components[0].argv
    assert "--paper" in engine and engine[engine.index("--db") + 1].endswith("paper.db")
    assert plan.components[1].url == "http://127.0.0.1:8765"


def test_missing_extras_are_skipped_with_an_explanation_not_an_error(tmp_path):
    plan = plan_components(args(tmp_path), {}, port_free=FREE)
    assert [c.name for c in plan.components] == ["moteur"]
    assert any("DASHBOARD_PASSWORD" in w for w in plan.warnings) and any("GROQ" in w for w in plan.warnings)


def test_a_short_dashboard_password_is_not_accepted(tmp_path):
    plan = plan_components(args(tmp_path), {"DASHBOARD_PASSWORD": 4}, port_free=FREE)
    assert "tableau de bord" not in [c.name for c in plan.components] and any("8 caractères" in w for w in plan.warnings)


def test_extras_can_be_disabled_explicitly(tmp_path):
    present = {"DASHBOARD_PASSWORD": 12, "GROQ_API_KEY": 30, "GROQ_MODEL": 8}
    plan = plan_components(args(tmp_path, no_dashboard=True, no_assistant=True), present, port_free=FREE)
    assert [c.name for c in plan.components] == ["moteur"]


def test_testnet_plan_requires_the_testnet_keys(tmp_path):
    with pytest.raises(ConfigError, match="BINANCE_TESTNET_API_KEY"):
        plan_components(args(tmp_path, mode="testnet"), {"DASHBOARD_PASSWORD": 12}, port_free=FREE)
    plan = plan_components(args(tmp_path, mode="testnet"), {"BINANCE_TESTNET_API_KEY": 64, "BINANCE_TESTNET_API_SECRET": 64}, port_free=FREE)
    engine = plan.components[0].argv
    assert "--paper" not in engine and engine[engine.index("--db") + 1].endswith("testnet.db")


def test_live_is_never_started_by_the_global_launcher(tmp_path):
    with pytest.raises(ConfigError, match="réel"):
        plan_components(args(tmp_path, config=str(settings_file(tmp_path, env="live"))), {}, port_free=FREE)
    with pytest.raises(ConfigError, match="mode"):
        plan_components(args(tmp_path, mode="live"), {}, port_free=FREE)


def test_a_config_without_any_strategy_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="aucune stratégie"):
        plan_components(args(tmp_path, config=str(settings_file(tmp_path, strategies=False))), {}, port_free=FREE)


def test_a_busy_dashboard_port_is_reported_clearly(tmp_path):
    plan = plan_components(args(tmp_path), {"DASHBOARD_PASSWORD": 12}, port_free=lambda p: False)
    assert "tableau de bord" not in [c.name for c in plan.components] and any("8765" in w and "occupé" in w for w in plan.warnings)


def test_child_environments_do_not_leak_secrets_between_components(tmp_path):
    present = {"DASHBOARD_PASSWORD": 12, "GROQ_API_KEY": 30, "GROQ_MODEL": 8}
    plan = plan_components(args(tmp_path), present, port_free=FREE)
    dash = plan.components[1]
    assert dash.env is not None and not any(k.startswith(("BINANCE_", "GROQ_", "TELEGRAM_")) for k in dash.env)


def test_read_env_names_returns_lengths_never_values(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nBINANCE_TESTNET_API_KEY=abcdef123456\nEMPTY=\nGROQ_MODEL='modele'\nBAD LINE\n")
    names = read_env_names(f)
    assert names == {"BINANCE_TESTNET_API_KEY": 12, "GROQ_MODEL": 6}
    assert "abcdef123456" not in repr(names)
    assert read_env_names(tmp_path / "absent") == {}
