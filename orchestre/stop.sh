#!/usr/bin/env bash
# Arrête proprement tout ce qu'a lancé start.sh (moteur, tableau de bord, assistant).
cd "$(dirname "$0")" || exit 1
exec ./orchestre.sh down "$@"
