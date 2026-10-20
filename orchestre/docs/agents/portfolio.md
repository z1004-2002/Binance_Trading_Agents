# Carte d'agent — PortfolioAgent (allocateur)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `portfolio` |
| Rôle | Répartir le capital plafonné entre stratèges selon le régime (v1) ou le rendement ajusté du risque (v2), via le Contract Net. |
| Type d'agent (Russell & Norvig) | Fondé sur l'utilité |
| Écrit sur Binance ? | Non |
| Code | `portfolio/ledger.py`, `portfolio/allocator.py`, `portfolio/allocator_v2.py`, `portfolio/contract_net.py` |
| Tests | `tests/unit/test_ledger.py`, `tests/unit/test_allocator.py`, `tests/unit/test_allocator_v2.py`, `tests/unit/test_contract_net.py` |
| Exigences de la spécification | EX-AL-01 à EX-AL-05 |

## PEAS

- **Performance** : capital conservé (la somme des parts égale le total) ; budgets conformes au régime ; drawdown contenu.
- **Environnement** : budgets configurés, régime de BTC, performances récentes.
- **Actionneurs** : allocations, refus motivés des offres sous leur minimum viable.
- **Capteurs** : régime publié, offres des stratèges.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | alloue seul, sans intervention. |
| Réactivité | recalcule les budgets au changement de régime. |
| Pro-activité | v2 : recalcul hebdomadaire d'après les rendements des 60 derniers jours. |
| Capacité sociale | protocole Contract Net : appel d'offres, offres, acceptations/refus sur le bus. |

## Paramètres

v1 : budget fixe × multiplicateur de régime (tableau §12.1), réserve de trésorerie 20 %, un agent sous son minimum viable reste en PAPER. v2 : poids ∝ Sortino⁺ / volatilité × multiplicateur, plafond 60 % par agent, variation ≤ 10 points par semaine, un plafond abaissé s'applique tout de suite.

## Régimes favorables et défavorables

Sans objet.

## Conditions d'échec

La v2 n'est PAS branchée en direct : le backtest d'équipe la montre moins sûre que la v1 (drawdown 22 % contre 14 %). Une baisse de budget ne descend jamais sous le montant engagé.

## Résultats mesurés

Équipe v1 : +31,9 %, drawdown 14,0 %, Sortino 1,44. Équipe v2 : +35,6 %, drawdown 22,0 %, Sortino 1,28. Détention de BTC : +34,6 %, drawdown 53,0 %, Sortino 0,82 (749 j, approximation optimiste : coût de réallocation non modélisé).

## Statut dans le cycle de vie

v1 en service ; v2 en BACKTEST.
