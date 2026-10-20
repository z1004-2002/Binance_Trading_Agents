# Carte d'agent — S-3 Suivi de tendance

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `trend` |
| Rôle | Être investi (acheteur seulement) quand le rendement sur 90 jours est positif et le prix au-dessus de sa moyenne 100 jours, avec un stop suiveur. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Via l'exécuteur uniquement |
| Code | `strategies/trend.py`, `strategies/exposure_runner.py` |
| Tests | `tests/unit/test_trend.py`, `tests/scenarios/test_exposure_runners.py` |
| Exigences de la spécification | §11.4, EX-SEC-14 |

## PEAS

- **Performance** : rendement ajusté du risque supérieur à la détention, drawdown plus faible.
- **Environnement** : bougies journalières d'une paire.
- **Actionneurs** : exposition cible [0 ; 1] et stop de protection chez Binance.
- **Capteurs** : bougies clôturées.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | décide seule à chaque bougie clôturée. |
| Réactivité | le stop suiveur ferme la position si le prix retombe (3 × ATR sous le plus haut). |
| Pro-activité | ciblage de volatilité : réduit l'exposition quand le marché s'agite. |
| Capacité sociale | budget fixé par l'allocateur selon le régime. |

## Paramètres

Rendement 90 j > 0 et prix > moyenne 100 j ; stop = plus haut depuis l'entrée − 3 × ATR(14), à cliquet (ne descend jamais) ; exposition = facteur de volatilité (cible 40 %) ; pas de ré-entrée immédiate après un stop.

## Régimes favorables et défavorables

Favorable : TENDANCE_HAUSSE (1) ; RANGE et HAUTE_VOLATILITÉ : 0,5 ; TENDANCE_BAISSE : 0.

## Conditions d'échec

Marchés sans direction : faux départs répétés et frais (57 transactions, 29 USDT de frais sur 1 000).

## Résultats mesurés

Seul : −11,2 % (drawdown 17,4 %). Walk-forward hors échantillon : −14,9 % (drawdown 16,3 %) contre −41,8 % pour la détention ; 45 essais ; scores d'entraînement positifs mais tests négatifs → non validé.

## Statut dans le cycle de vie

BACKTEST (non promu : résultat hors échantillon négatif).
