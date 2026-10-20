# Carte d'agent — S-1 DCA (achat périodique)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `dca` |
| Rôle | Acheter un montant fixe à intervalle régulier, avec une variante modulée par l'indice Fear & Greed. |
| Type d'agent (Russell & Norvig) | Réflexe simple déclenché par une horloge |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/dca.py`, `strategies/dca_runner.py` |
| Tests | `tests/unit/test_dca.py`, `tests/scenarios/test_dca_runner.py` |
| Exigences de la spécification | EX-ST-01, EX-ST-02 |

## PEAS

- **Performance** : prix moyen d'acquisition frais inclus ≤ prix moyen du marché ; zéro achat manqué ou en double.
- **Environnement** : une paire Spot.
- **Actionneurs** : ordre au marché en montant de cotation.
- **Capteurs** : horloge, prix, indice de sentiment.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | achète seul à chaque échéance. |
| Réactivité | applique la politique d'achat manqué (sauter ou un seul rattrapage). |
| Pro-activité | aucune (réflexe pur). |
| Capacité sociale | reçoit son budget de l'allocateur et l'indice du SentimentAgent. |

## Paramètres

Montant par achat (ex. 20 USDT), intervalle (ex. 7 jours), plafond cumulé, politique d'achat manqué `skip` ou `catchup_once`, variante modulée (× 1,5 si l'indice < 25, × 0,5 s'il > 75).

## Régimes favorables et défavorables

Tous les régimes (multiplicateur 1 partout).

## Conditions d'échec

Il accumule aussi pendant une longue baisse ; aucun stop ; il ne vend jamais (politique au kill switch : conserver).

## Résultats mesurés

Exemple chiffré de la spécification reproduit (prix moyen ≈ 48 980 contre 50 000). Pas de backtest dédié : stratégie à ordres, hors du moteur à exposition cible. Paper trading réel de 2 ticks : un achat de 20 USDT effectué.

## Statut dans le cycle de vie

BACKTEST (aucune simulation de 30 jours terminée).
