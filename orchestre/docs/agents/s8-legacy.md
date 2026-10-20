# Carte d'agent — S-8 Agents du système existant (adaptateur)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Aucun résultat de backtest n'existe pour cet agent : il n'a pas de rendement mesuré. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `legacy` |
| Rôle | Faire passer un agent hérité par le risque et l'exécuteur : il propose des intentions (acheter ou vendre), jamais des ordres directs. |
| Type d'agent (Russell & Norvig) | Selon l'agent adapté (non audité) |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/legacy.py`, `audit/legacy.py` |
| Tests | `tests/scenarios/test_extra_strategies.py`, `tests/unit/test_audit_legacy.py` |
| Exigences de la spécification | §11.9, EX-SEC-05 |

## PEAS

- **Performance** : selon l'agent ; ici, qu'il ne contourne jamais le risque.
- **Environnement** : instantané des prix et des positions de ses paires.
- **Actionneurs** : intentions validées puis ordres au marché via RiskAgent et ExecutionAgent.
- **Capteurs** : instantané (prix, stock) fourni par l'adaptateur.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide à chaque tick selon son propre code. |
| Réactivité | ses exceptions sont contenues ; il ne peut pas arrêter l'équipe. |
| Pro-activité | propose seulement ; l'adaptateur refuse un symbole non autorisé, une vente supérieure au stock, un prix limite. |
| Capacité sociale | budget fixé par la configuration ; pas de modulation par régime tant qu'il n'est pas audité. |

## Paramètres

`path` (un fichier), `class`, `symbols`, `budget_quote`. Le fichier est audité avant import : clé en clair, retrait ou ordre direct → refusé. Activable par la configuration uniquement, jamais depuis l'interface (il charge du code).

## Régimes favorables et défavorables

Sans objet : budget plein, sans multiplicateur.

## Conditions d'échec

Tout ce que l'audit ne voit pas : un code hérité peut être sans stop ni limite de perte propres. Le risque du moteur reste le seul garde-fou.

## Résultats mesurés

Aucun : le code du système existant n'a pas été retrouvé (voir `docs/audit-existant.md`).

## Statut dans le cycle de vie

Adaptateur prêt, aucun agent branché ; chaque agent adapté suit le cycle de vie complet avant le réel.
