# Carte d'agent — S-5 Retour à la moyenne

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `meanreversion` |
| Rôle | Acheter un excès baissier (sous la bande de Bollinger basse avec RSI bas) et sortir au retour à la moyenne. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/meanreversion.py`, `strategies/exposure_runner.py` |
| Tests | `tests/unit/test_meanreversion.py` |
| Exigences de la spécification | §11.6, EX-SEC-14 |

## PEAS

- **Performance** : gains rapides en marché sans direction, pertes limitées par le stop.
- **Environnement** : bougies de 4 heures d'une paire.
- **Actionneurs** : exposition 1 ou 0 et stop.
- **Capteurs** : bougies clôturées.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide seule. |
| Réactivité | stop à 1,5 × ATR et sortie forcée après 30 bougies. |
| Pro-activité | aucune. |
| Capacité sociale | actif seulement en RANGE (multiplicateur 0 ailleurs). |

## Paramètres

Entrée : clôture sous Bollinger(20 ; 2) ET RSI(14) < 30 ; sortie : retour à la moyenne 20 ou RSI > 50 ; stop 1,5 × ATR(14) ; 30 bougies maximum ; calculs bornés à une fenêtre de 141 bougies (écart < 0,05).

## Régimes favorables et défavorables

Favorable : RANGE seulement.

## Conditions d'échec

Acheter une baisse qui continue en tendance baissière ; frais élevés (30 transactions pour 30 USDT).

## Résultats mesurés

Walk-forward 4 h hors échantillon : +0,1 % (drawdown 13,2 %) contre −24,3 % pour la détention ; 45 essais → rendement quasi nul, non validé.

## Statut dans le cycle de vie

BACKTEST (non promu).
