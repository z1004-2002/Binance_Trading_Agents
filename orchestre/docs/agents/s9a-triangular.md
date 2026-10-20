# Carte d'agent — S-9a Arbitrage triangulaire (observateur)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Aucun résultat de backtest n'existe pour cet agent : il n'a pas de rendement mesuré. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `triangular` |
| Rôle | Mesurer, pour un triangle USDT → BTC → ETH → USDT (et son sens inverse), l'écart net après frais et glissement sur la profondeur réelle du carnet. N'envoie aucun ordre. |
| Type d'agent (Russell & Norvig) | Réflexe simple (capteur seulement) |
| Écrit sur Binance ? | Jamais |
| Code | `strategies/triangular.py` |
| Tests | `tests/unit/test_triangular.py`, `tests/scenarios/test_extra_strategies.py` |
| Exigences de la spécification | §11.10, EX-ST-10 |

## PEAS

- **Performance** : nombre d'écarts nets positifs observés et taille du meilleur écart.
- **Environnement** : carnets d'ordres de trois paires.
- **Actionneurs** : aucun : une alerte d'information seulement.
- **Capteurs** : carnets d'ordres (profondeur, pas seulement le prix).

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | scrute à intervalle régulier sans intervention. |
| Réactivité | relit les carnets à chaque passage. |
| Pro-activité | signale seulement ; ne cherche pas à exécuter. |
| Capacité sociale | aucun budget : ne dépend pas de l'allocateur. |

## Paramètres

paires `a`, `b` (ex. BTC, ETH), `notional`, `threshold` ; frais de 0,1 % par transaction (environ 0,3 % pour trois).

## Régimes favorables et défavorables

sans objet : l'observateur ne gouverne aucun budget.

## Conditions d'échec

Jamais exploitable ici : l'écart doit dépasser le coût de trois transactions et le glissement, et des acteurs plus rapides l'effacent. Un écart observé n'est pas un gain réalisable.

## Résultats mesurés

Aucun rendement : agent pédagogique de mesure.

## Statut dans le cycle de vie

Expérimental, observation uniquement (ne passe jamais en réel : il n'a rien à exécuter).
