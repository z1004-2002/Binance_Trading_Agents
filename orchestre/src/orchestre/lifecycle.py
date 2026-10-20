"""Cycle de vie d'une stratégie (§14, EX-LC-02) : IDÉE -> BACKTEST -> PAPER -> LIVE_PILOTE -> LIVE -> RETIRÉ.

Une promotion avance d'UNE étape à la fois et reste une décision humaine ; entrer en LIVE_PILOTE ou LIVE exige en plus une
confirmation explicite et la décision écrite (docs/decision-live.md). Une rétrogradation (réduction de risque) est libre.
Toutes les transitions sont journalisées avec leur raison.
"""

from __future__ import annotations

from datetime import datetime

from orchestre.persistence.store import Store

STAGES = ["IDÉE", "BACKTEST", "PAPER", "LIVE_PILOTE", "LIVE", "RETIRÉ"]
LIVE_STAGES = {"LIVE_PILOTE", "LIVE"}
CODES = ["S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-7"]
DEFAULT_STAGE = "BACKTEST"   # aucune stratégie n'a encore passé les 30 jours de simulation


class LifecycleError(ValueError):
    pass


def current_stage(store: Store, code: str) -> str:
    events = store.lifecycle_events(code)
    return events[0]["to_stage"] if events else DEFAULT_STAGE


def history(store: Store, code: str) -> list[dict]:
    return store.lifecycle_events(code)


def set_stage(
    store: Store, code: str, to_stage: str, reason: str, decided_by: str, now: datetime,
    confirm: bool = False, decision_exists: bool = False,
) -> None:
    if to_stage not in STAGES:
        raise LifecycleError(f"étape inconnue : {to_stage} (étapes : {', '.join(STAGES)})")
    if not reason.strip():
        raise LifecycleError("une raison est obligatoire (EX-LC-02)")
    frm = current_stage(store, code)
    if frm == "RETIRÉ":
        raise LifecycleError(f"{code} est retirée : aucune transition possible")
    i, j = STAGES.index(frm), STAGES.index(to_stage)
    if j == i:
        raise LifecycleError(f"{code} est déjà à l'étape {frm}")
    if j > i and to_stage != "RETIRÉ":
        if j != i + 1:
            raise LifecycleError(f"une promotion avance d'une seule étape à la fois ({frm} -> {STAGES[i + 1]})")
        if to_stage in LIVE_STAGES:
            if not confirm:
                raise LifecycleError(f"l'entrée en {to_stage} exige --confirm (décision humaine)")
            if not decision_exists:
                raise LifecycleError("l'entrée en réel exige la décision écrite (docs/decision-live.md)")
    store.log_lifecycle(now, code, frm, to_stage, reason, decided_by)
