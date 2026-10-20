# Carte d'agent — S-9b Tenue de marché (simulation)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Aucun résultat de backtest n'existe pour cet agent : il n'a pas de rendement mesuré. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `marketmaker` |
| Rôle | Coter un achat sous le prix milieu et une vente au-dessus pour capter l'écart, avec un plafond de stock. Simulation uniquement. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle (stock et prix milieu) |
| Écrit sur Binance ? | Via l'exécuteur uniquement (simulation seulement) |
| Code | `strategies/marketmaker.py` |
| Tests | `tests/scenarios/test_extra_strategies.py`, `tests/scenarios/test_orchestrator_extra.py` |
| Exigences de la spécification | §11.10, EX-ST-10 |

## PEAS

- **Performance** : écart capté moins les pertes de stock, après frais.
- **Environnement** : prix milieu et ordres passifs d'une paire.
- **Actionneurs** : ordres limites passifs simulés (achat et vente), annulés et recotés si le prix s'éloigne.
- **Capteurs** : prix, stock détenu, ordres suivis.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | recote seule. |
| Réactivité | annule et recote quand le prix s'écarte de plus du seuil ; suspendue : annule tout. |
| Pro-activité | maintient deux ordres autour du prix. |
| Capacité sociale | budget fixé par l'allocateur (comme le Grid : 0,5 en hausse, 1 en range, 0 sinon). |

## Paramètres

`half_spread` (0,3 %), `quote_size_pct`, `max_inventory_pct`. Spot : pas de vente sans stock. Refusée si l'exchange n'est pas l'exchange paper (le moteur lève une erreur) et, en réel, sans `promoted = true`.

## Régimes favorables et défavorables

Favorable : RANGE (1) ; TENDANCE_HAUSSE : 0,5 ; TENDANCE_BAISSE et HAUTE_VOLATILITÉ : 0.

## Conditions d'échec

Sélection adverse : en tendance, on accumule le mauvais côté. En simulation, les ordres passifs sont remplis aux prix relevés à chaque tick, ce qui est optimiste (pas de file d'attente réelle).

## Résultats mesurés

Aucun résultat mesuré : le simulateur ne prouve rien sur la rentabilité réelle.

## Statut dans le cycle de vie

Simulation uniquement ; jamais promu en réel sans décision écrite (SPEC §11.10).
