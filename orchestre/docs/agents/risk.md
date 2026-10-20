# Carte d'agent — RiskAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `risk` |
| Rôle | Accepter ou rejeter chaque proposition d'ordre selon 16 règles ordonnées ; seul son accord permet un ordre. |
| Type d'agent (Russell & Norvig) | Fondé sur des buts (contraintes) |
| Écrit sur Binance ? | Non (il décide sans agir) |
| Code | `risk/engine.py`, `risk/models.py` |
| Tests | `tests/unit/test_risk.py`, `tests/scenarios/test_ac_team_01.py` |
| Exigences de la spécification | EX-SEC-12, EX-RK-01, R-01 à R-16, §12.3 |

## PEAS

- **Performance** : zéro ordre hors des limites ; refus motivés ; zéro faux refus d'une sortie de risque.
- **Environnement** : propositions des stratèges et instantané du contexte (budgets, soldes, exposition, drawdown).
- **Actionneurs** : décision signée (`ApprovedOrder`) ou refus avec règle et raison.
- **Capteurs** : contexte de risque construit à chaque ordre par l'orchestrateur.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide seul, sans intervention humaine. |
| Réactivité | réagit à chaque proposition avec le contexte du moment. |
| Pro-activité | aucune : il ne prend pas d'initiative, il filtre. |
| Capacité sociale | les stratèges le consultent ; l'exécuteur n'accepte que ses décisions. |

## Paramètres

R-01 kill switch ; R-02 environnement ; R-03 budget ; R-04 plafond et réserve de trésorerie 20 % ; R-05 solde ; R-06 valeur min/max (100 USDT par défaut) ; R-07 prix limite à 2 % du dernier prix (sauf stops) ; R-08 paire liquide, autorisée et non en pause ; R-09 40 % par actif ; R-10 80 % en cryptos ; R-11 stop obligatoire pour une position à exposition cible ; R-12 ordres ouverts ; R-13 paliers de drawdown (10 % : plus d'achat ; perte du jour 3 %) ; R-14 disjoncteur ; R-15 ordres par jour ; R-16 levier et instruments. Une réduction de risque ne passe que R-01, R-02, R-07 ; une sortie d'urgence passe même le kill switch (C-08).

## Régimes favorables et défavorables

Sans objet (garde-fou).

## Conditions d'échec

Il dépend de la qualité du contexte que lui fournit l'orchestrateur ; la vérification de la clé et des permissions est faite ailleurs (EX-SEC-05). Les règles R-17 à R-20 (instruments avancés) ne sont pas implémentées.

## Résultats mesurés

Couverture par tests de chaque règle, de l'ordre d'évaluation et du scellé de l'ordre approuvé ; scénario de krach de −30 % (AC-TEAM-01) passé.

## Statut dans le cycle de vie

Composant d'infrastructure critique.
