# Carte d'agent — S-6 Rotation momentum

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `rotation` |
| Rôle | Chaque semaine, détenir les 3 cryptos au meilleur rendement sur 21 jours à poids égaux ; tout en liquidités si BTC est en tendance baissière. |
| Type d'agent (Russell & Norvig) | Fondé sur l'utilité (classement) |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/rotation.py`, `strategies/exposure_runner.py` (PortfolioRunner) |
| Tests | `tests/unit/test_portfolio_engine.py` |
| Exigences de la spécification | §11.7, EX-SEC-14 |

## PEAS

- **Performance** : rendement ajusté du risque supérieur au panier détenu.
- **Environnement** : plusieurs paires Spot.
- **Actionneurs** : poids par actif, stops de protection par défaut (25 % sous l'entrée, non backtestés).
- **Capteurs** : bougies journalières de plusieurs paires.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide seule chaque semaine. |
| Réactivité | passe en liquidités quand le régime de BTC est baissier. |
| Pro-activité | classe l'univers et réalloue. |
| Capacité sociale | budget fixé par l'allocateur. |

## Paramètres

K = 3, rendement sur 21 jours, rééquilibrage tous les 7 jours, univers fourni par la configuration.

## Régimes favorables et défavorables

Favorable : TENDANCE_HAUSSE (1) ; RANGE et HAUTE_VOLATILITÉ : 0,5 ; BAISSE : 0.

## Conditions d'échec

Biais du survivant (univers = cryptos existant aujourd'hui) ; beaucoup de transactions (195, 113 USDT de frais sur 1 000) ; le budget minimal viable de plusieurs positions est souvent hors de portée d'un très petit capital.

## Résultats mesurés

Seul : +149,0 % (drawdown 50,1 %). Modèle nul de 200 rotations au hasard (mêmes règles) : médiane +52,9 %, centile 10–90 de 14,5 % à 99,3 % ; S-6 bat 100 % des tirages → le momentum apporte quelque chose, mais plus de la moitié du gain vient de l'univers et de la règle « liquidités si BTC baissier ».

## Statut dans le cycle de vie

BACKTEST (candidat à examiner, non validé : biais du survivant, drawdown élevé).
