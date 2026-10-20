# Carte d'agent — SupervisorAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `supervisor` |
| Rôle | Surveiller la santé de l'équipe, appliquer pause / reprise / arrêt d'urgence, faire l'autocontrôle quotidien. |
| Type d'agent (Russell & Norvig) | Agent de contrôle |
| Écrit sur Binance ? | Indirectement (annulations d'urgence via l'exécuteur) |
| Code | `supervisor/service.py`, `runtime/orchestrator.py` |
| Tests | `tests/unit/test_supervisor.py`, `tests/scenarios/test_orchestrator*.py`, `tests/scenarios/test_ac_team_01.py` |
| Exigences de la spécification | EX-SU-01 à EX-SU-03, EX-SEC-13, EX-SEC-15, EX-ENV-02 |

## PEAS

- **Performance** : un agent défaillant ne fait pas tomber les autres ; arrêt d'urgence immédiat ; zéro position sans stop.
- **Environnement** : les agents et l'exchange.
- **Actionneurs** : pause, reprise, kill, relance, alertes, autocontrôle.
- **Capteurs** : battements de cœur, fichier STOP, table de commandes, réconciliation.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | agit seul sur les pannes et sur les paliers de drawdown. |
| Réactivité | un agent critique silencieux > 5 min met les stratèges en pause ; une remise à zéro du testnet est détectée. |
| Pro-activité | autocontrôle quotidien (registre, stops, disque) sans sollicitation. |
| Capacité sociale | reçoit les commandes du porteur via le tableau de bord. |

## Paramètres

Délai de silence 5 min ; paliers 5 % (budgets divisés par deux), 10 % (plus d'achat), 15 % (arrêt global et liquidation des positions à exposition cible) ; arrêt déclenché aussi par le fichier `var/STOP`.

## Régimes favorables et défavorables

Sans objet.

## Conditions d'échec

Après l'arrêt global le programme ne surveille plus : un stop-limit qui ne s'exécute pas ensuite n'est plus rattrapé par le garde-fou logiciel ; une IP bannie empêche les sorties d'urgence.

## Résultats mesurés

Scénario AC-TEAM-01 (krach −30 % en une heure) : stops exécutés, aucune nouvelle position, alertes émises, aucun plafond dépassé.

## Statut dans le cycle de vie

Composant d'infrastructure critique.
