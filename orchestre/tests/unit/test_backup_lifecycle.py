import io
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestre.backup import BackupError, make_backup
from orchestre.cli.main import main
from orchestre.lifecycle import STAGES, LifecycleError, current_stage, history, set_stage
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def live_db(tmp_path):
    db = tmp_path / "o.db"
    store = Store(Database(db))
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D("100"))
    store.save_all(ledger, [], {"dca": {"total_spent": "20"}}, T0)
    store.log_event(T0, "INFO", "x", "donnée écrite mais pas encore checkpointée (WAL)")
    return db, store


# ---------- sauvegarde ----------

def test_backup_is_consistent_and_readable_while_the_database_is_in_use(tmp_path):
    db, store = live_db(tmp_path)
    path = make_backup(db, tmp_path / "bk", keep=5, now=T0)
    assert path.name == "orchestre-20261020T120000Z.db"
    copy = sqlite3.connect(path)
    assert copy.execute("select count(*) from events").fetchone()[0] == 1
    assert copy.execute("select allocated from ledger where agent_id = 'dca'").fetchone()[0] == "100"
    assert copy.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_old_backups_are_pruned(tmp_path):
    db, _ = live_db(tmp_path)
    for i in range(6):
        make_backup(db, tmp_path / "bk", keep=3, now=T0 + timedelta(hours=i))
    names = sorted(p.name for p in (tmp_path / "bk").glob("*.db"))
    assert len(names) == 3 and names[-1] == "orchestre-20261020T170000Z.db"


def test_backup_refuses_a_missing_database_and_never_overwrites(tmp_path):
    with pytest.raises(BackupError):
        make_backup(tmp_path / "absente.db", tmp_path / "bk", keep=3, now=T0)
    db, _ = live_db(tmp_path)
    make_backup(db, tmp_path / "bk", keep=3, now=T0)
    with pytest.raises(BackupError, match="existe déjà"):
        make_backup(db, tmp_path / "bk", keep=3, now=T0)


def test_cli_backup(tmp_path):
    db, _ = live_db(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = main(["backup", "--db", str(db), "--dest", str(tmp_path / "bk"), "--keep", "2"], environ={}, out=out, err=err)
    assert code == 0 and "Sauvegarde" in out.getvalue() and list((tmp_path / "bk").glob("*.db"))
    assert main(["backup", "--db", str(tmp_path / "x.db"), "--dest", str(tmp_path / "bk")], environ={}, out=io.StringIO(), err=io.StringIO()) == 2


# ---------- cycle de vie ----------

def fresh():
    return Store(Database(":memory:"))


def test_every_strategy_starts_at_backtest_because_none_has_passed_the_gates():
    s = fresh()
    assert current_stage(s, "S-3") == "BACKTEST" and current_stage(s, "S-1") == "BACKTEST"


def test_a_promotion_moves_exactly_one_step_and_is_recorded():
    s = fresh()
    set_stage(s, "S-3", "PAPER", "walk-forward satisfaisant", "porteur", T0)
    assert current_stage(s, "S-3") == "PAPER"
    h = history(s, "S-3")
    assert h[0]["from_stage"] == "BACKTEST" and h[0]["to_stage"] == "PAPER" and h[0]["reason"] == "walk-forward satisfaisant"
    with pytest.raises(LifecycleError, match="une seule étape"):
        set_stage(s, "S-3", "LIVE", "trop vite", "porteur", T0, confirm=True, decision_exists=True)


def test_promotions_to_live_stages_need_confirmation_and_a_written_decision():
    s = fresh()
    set_stage(s, "S-3", "PAPER", "ok", "porteur", T0)
    with pytest.raises(LifecycleError, match="confirm"):
        set_stage(s, "S-3", "LIVE_PILOTE", "ok", "porteur", T0)
    with pytest.raises(LifecycleError, match="décision écrite"):
        set_stage(s, "S-3", "LIVE_PILOTE", "ok", "porteur", T0, confirm=True, decision_exists=False)
    set_stage(s, "S-3", "LIVE_PILOTE", "ok", "porteur", T0, confirm=True, decision_exists=True)
    assert current_stage(s, "S-3") == "LIVE_PILOTE"


def test_demotion_is_allowed_to_any_lower_stage_without_ceremony_and_a_reason_is_required():
    s = fresh()
    set_stage(s, "S-3", "PAPER", "ok", "porteur", T0)
    set_stage(s, "S-3", "LIVE_PILOTE", "ok", "porteur", T0, confirm=True, decision_exists=True)
    set_stage(s, "S-3", "BACKTEST", "drawdown anormal", "automatique", T0 + timedelta(days=1))
    assert current_stage(s, "S-3") == "BACKTEST"
    with pytest.raises(LifecycleError, match="raison"):
        set_stage(s, "S-3", "RETIRÉ", " ", "porteur", T0)


def test_unknown_stages_and_retired_strategies_are_refused():
    s = fresh()
    with pytest.raises(LifecycleError, match="étape"):
        set_stage(s, "S-3", "PRODUCTION", "x", "porteur", T0)
    set_stage(s, "S-3", "RETIRÉ", "abandonnée", "porteur", T0)
    with pytest.raises(LifecycleError, match="retirée"):
        set_stage(s, "S-3", "PAPER", "x", "porteur", T0)
    assert STAGES == ["IDÉE", "BACKTEST", "PAPER", "LIVE_PILOTE", "LIVE", "RETIRÉ"]


def test_cli_lifecycle(tmp_path):
    db = tmp_path / "l.db"
    Store(Database(db))
    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        return main(list(argv), environ={}, out=out, err=err), out.getvalue(), err.getvalue()
    code, out, _ = run("lifecycle", "show", "--db", str(db))
    assert code == 0 and "S-3" in out and "BACKTEST" in out
    assert run("lifecycle", "set", "S-3", "PAPER", "--reason", "essai", "--db", str(db))[0] == 0
    assert "PAPER" in run("lifecycle", "show", "--db", str(db))[1]
    code, _, err = run("lifecycle", "set", "S-3", "LIVE_PILOTE", "--reason", "x", "--db", str(db))
    assert code == 2 and "confirm" in err
