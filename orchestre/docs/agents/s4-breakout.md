# Carte d'agent — S-4 Cassure de canal (Donchian)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `breakout` |
| Rôle | Entrer quand la clôture dépasse le plus haut des 20 derniers jours, sortir sous le plus bas des 10 derniers. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/breakout.py`, `strategies/exposure_runner.py` |
| Tests | `tests/unit/test_breakout.py` |
| Exigences de la spécification | §11.5, EX-SEC-14 |

## PEAS

- **Performance** : capter le début des tendances en limitant les pertes.
- **Environnement** : bougies journalières d'une paire.
- **Actionneurs** : exposition 1 ou 0 et stop initial.
- **Capteurs** : bougies clôturées.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide seule. |
| Réactivité | le stop initial et la sortie sur canal protègent la position. |
| Pro-activité | aucune. |
| Capacité sociale | budget fixé par l'allocateur. |

## Paramètres

Entrée : clôture > plus haut des 20 derniers jours ; sortie : clôture < plus bas des 10 derniers ; stop initial 2 × ATR(14) sous l'entrée, conservé (cliquet) ; l'état « en position » est sauvegardé pour survivre à un redémarrage.

## Régimes favorables et défavorables

Favorable : TENDANCE_HAUSSE et RANGE (1) ; HAUTE_VOLATILITÉ : 0,5 ; BAISSE : 0.

## Conditions d'échec

Fausses cassures en marché sans direction.

## Résultats mesurés

Seul : +45,2 % (drawdown 28,5 %) mais hors échantillon : −16,1 % (drawdown 22,7 %), 40 essais → écart considérable entre les deux, non validé.

## Statut dans le cycle de vie

BACKTEST (non promu).
