#!/usr/bin/env bash
# Lance TOUT le système : moteur + tableau de bord + assistant (selon ce que contient .env).
#   ./start.sh                 simulation (paper) sur le marché réel, aucun ordre réel
#   ./start.sh testnet         ordres sur le testnet Binance (argent virtuel)
#   ./start.sh paper --detach  en arrière-plan
# Le mode réel n'est jamais lancé par ce script.
cd "$(dirname "$0")" || exit 1
mode=paper
if [[ "${1:-}" == paper || "${1:-}" == testnet ]]; then mode="$1"; shift; fi
exec ./orchestre.sh up --mode "$mode" "$@"
