"""Régimes de marché (EX-AN-05)."""

from enum import Enum


class Regime(str, Enum):
    TENDANCE_HAUSSE = "TENDANCE_HAUSSE"
    TENDANCE_BAISSE = "TENDANCE_BAISSE"
    RANGE = "RANGE"
    HAUTE_VOLATILITE = "HAUTE_VOLATILITE"
