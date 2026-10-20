# Carte d'agent — S-7 Rééquilibrage à poids constants

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `rebalance` |
| Rôle | Garder des poids fixes (ex. 50 % BTC / 50 % liquidités) en rééquilibrant quand un poids dérive de plus de 5 points ou chaque mois. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/rotation.py` (`ConstantMix`), `strategies/exposure_runner.py` |
| Tests | `tests/unit/test_portfolio_engine.py` |
| Exigences de la spécification | §11.8, EX-SEC-14 |

## PEAS

- **Performance** : réduire le drawdown en captant la prime de rééquilibrage.
- **Environnement** : un ou plusieurs actifs Spot.
- **Actionneurs** : poids cibles, stops de protection par défaut.
- **Capteurs** : bougies journalières.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | rééquilibre seul. |
| Réactivité | réagit à la dérive des poids. |
| Pro-activité | aucune. |
| Capacité sociale | budget fixé par l'allocateur. |

## Paramètres

Poids cibles (ex. BTCUSDT 0,5), bande 5 points, rééquilibrage mensuel.

## Régimes favorables et défavorables

Tous les régimes (multiplicateur 1).

## Conditions d'échec

Il subit les baisses de l'actif détenu (50 % de BTC) ; un seul actif limite la diversification.

## Résultats mesurés

+22,0 % (drawdown 29,6 %, Sortino 0,82) contre la détention de BTC +34,6 % (drawdown 53,0 %) : rendement plus faible, risque presque divisé par deux.

## Statut dans le cycle de vie

BACKTEST (référence B-3 de la spécification).
