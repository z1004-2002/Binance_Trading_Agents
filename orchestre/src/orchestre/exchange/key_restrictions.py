"""Contrôle des permissions de la clé live au démarrage (EX-SEC-05). Fonction pure, sans réseau."""

from __future__ import annotations

from typing import Mapping


def check_key_restrictions(
    restrictions: Mapping[str, object],
    enabled_instruments: Mapping[str, bool] | None = None,
) -> list[str]:
    """Retourne la liste des violations ; le programme DOIT refuser de démarrer si elle n'est pas vide."""
    enabled = enabled_instruments or {}
    violations: list[str] = []
    if restrictions.get("ipRestrict") is not True:
        violations.append("restriction IP absente")
    if restrictions.get("enableWithdrawals") is not False:
        violations.append("les retraits sont autorisés pour cette clé")
    if restrictions.get("enableInternalTransfer") is not False or restrictions.get("permitsUniversalTransfer") is not False:
        violations.append("les transferts sont autorisés pour cette clé")
    if restrictions.get("enableMargin") is True and not enabled.get("margin", False):
        violations.append("permission marge active alors que l'instrument est désactivé")
    if restrictions.get("enableFutures") is True and not enabled.get("futures", False):
        violations.append("permission futures active alors que l'instrument est désactivé")
    if restrictions.get("enableSpotAndMarginTrading") is not True:
        violations.append("permission de trading Spot absente")
    return violations
