#!/usr/bin/env bash
# Lanceur : ./orchestre.sh <commande> [options]   (voir docs/LANCEMENT.md)
cd "$(dirname "$0")" || exit 1
PYTHONPATH=src exec ../bin-env/bin/python -m orchestre.cli "$@"
