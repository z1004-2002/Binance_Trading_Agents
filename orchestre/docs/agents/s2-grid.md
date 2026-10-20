# Carte d'agent — S-2 Grid (grille)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `grid` |
| Rôle | Poser des achats sous le prix et revendre un niveau plus haut après chaque achat, pour capter les oscillations en marché sans direction. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle (mémoire de l'état de chaque niveau) |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/grid.py`, `strategies/grid_runner.py` |
| Tests | `tests/unit/test_grid.py`, `tests/scenarios/test_grid_cycles.py`, `tests/scenarios/test_restart.py` |
| Exigences de la spécification | EX-ST-03 à EX-ST-06 |

## PEAS

- **Performance** : profit net par cycle > frais ; zéro ordre orphelin ; temps passé dans la fourchette.
- **Environnement** : une paire dans une fourchette [L ; U].
- **Actionneurs** : ordres limites maker, annulations, rachat ou arrêt.
- **Capteurs** : prix, état des ordres, soldes.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | gère seule ses cycles achat → vente. |
| Réactivité | arrête la grille quand le prix sort de la fourchette (2 % sous la borne basse). |
| Pro-activité | réinvestit chaque exécution en un ordre au niveau voisin. |
| Capacité sociale | reçoit son budget de l'allocateur ; refus du RiskAgent consignés. |

## Paramètres

Bornes L et U, N intervalles, budget ; niveaux géométriques ; configuration refusée si le profit net par cycle est < 0,1 %, si N dépasse MAX_NUM_ORDERS ou si le budget est sous le minimum viable ; politique de sortie `pause`, `wait` ou `close` (vente de l'inventaire).

## Régimes favorables et défavorables

Favorable : RANGE (1) ; HAUTE_VOLATILITÉ et BAISSE : 0 ; HAUSSE : 0,5.

## Conditions d'échec

Une tendance baissière fait accumuler de l'inventaire qui perd ; trop de niveaux rend chaque cycle perdant (ex. 200 niveaux : écart 0,084 % < 0,2 % de frais) ; fourchette à recaler si le prix en sort.

## Résultats mesurés

Exemple de la spécification reproduit (écart 0,839 %, profit net 0,64 %). Cycles complets validés en scénario ; pas de backtest dédié (stratégie à ordres).

## Statut dans le cycle de vie

BACKTEST (aucune simulation de 30 jours terminée).
