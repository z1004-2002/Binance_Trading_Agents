# Carte d'agent — LiquidityAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `liquidity` |
| Rôle | Estimer l'écart achat/vente et le glissement pour la taille d'ordre, et marquer une paire « illiquide » au-delà du seuil. |
| Type d'agent (Russell & Norvig) | Réflexe simple |
| Écrit sur Binance ? | Non |
| Code | `analysis/liquidity.py` |
| Tests | `tests/unit/test_analysis.py` |
| Exigences de la spécification | EX-AN-12 |

## PEAS

- **Performance** : aucun ordre sur une paire dont le coût d'exécution explose.
- **Environnement** : carnet d'ordres Binance (100 niveaux).
- **Actionneurs** : verdict liquide / illiquide avec raison ; la règle R-08 du RiskAgent le consomme.
- **Capteurs** : carnet d'ordres.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | évalue seul chaque minute. |
| Réactivité | un carnet vide, large ou mince bascule immédiatement la paire en « illiquide ». |
| Pro-activité | rafraîchit sans sollicitation. |
| Capacité sociale | bloque indirectement les stratèges via le RiskAgent. |

## Paramètres

Écart maximal 0,3 % ; glissement maximal 0,3 % ; taille d'ordre de référence 50 USDT.

## Régimes favorables et défavorables

Sans objet (capteur).

## Conditions d'échec

Le glissement est estimé sur un carnet instantané ; une paire peut devenir illiquide entre deux relevés ; les ventes de réduction de risque ne sont jamais bloquées par ce verdict.

## Résultats mesurés

Tests : carnet profond, écart large, carnet mince, profondeur insuffisante, carnet vide.

## Statut dans le cycle de vie

Composant d'infrastructure.
