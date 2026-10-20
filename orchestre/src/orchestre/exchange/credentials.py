"""Lecture des clés par environnement (EX-SEC-02, EX-SEC-03). Ne journalise jamais de valeur."""

from __future__ import annotations

from typing import Mapping

from orchestre.config import ConfigError, Env


def _names(env: Env) -> tuple[str, str]:
    prefix = f"BINANCE_{env.name}"
    return f"{prefix}_API_KEY", f"{prefix}_API_SECRET"


def load_credentials(env: Env, environ: Mapping[str, str]) -> tuple[str, str]:
    key_name, secret_name = _names(env)
    key, secret = environ.get(key_name, ""), environ.get(secret_name, "")
    if not key or not secret:
        raise ConfigError(f"clés manquantes : définir {key_name} et {secret_name}")
    for other in Env:
        if other is env:
            continue
        other_key = environ.get(_names(other)[0], "")
        if other_key and other_key == key:
            raise ConfigError(f"mélange d'environnements : la clé {env.value} est réutilisée pour {other.value}")
    return key, secret
