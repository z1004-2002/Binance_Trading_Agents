# Carte d'agent — MarketDataAgent (cohérence des données)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `marketdata` |
| Rôle | Valider les bougies avant que quiconque les utilise : seules les bougies clôturées et cohérentes sont diffusées. |
| Type d'agent (Russell & Norvig) | Capteur partagé (fonction `validate_series`, appelée par le service d'analyse et les exécuteurs) |
| Écrit sur Binance ? | Non |
| Code | `analysis/marketdata.py`, `exchange/base.py` (`get_candles`) |
| Tests | `tests/unit/test_analysis.py`, `tests/scenarios/test_analysis_service.py` |
| Exigences de la spécification | EX-AN-01, EX-AN-02, EX-AN-03 |

## PEAS

- **Performance** : aucune donnée suspecte transmise ; trous détectés ; bougie en cours jamais diffusée.
- **Environnement** : API REST klines de Binance (marché réel ou testnet).
- **Actionneurs** : bougies acceptées, liste de problèmes, état « données suspectes » qui met les agents dépendants à l'arrêt.
- **Capteurs** : bougies OHLCV, horodatages.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | valide seul à chaque rafraîchissement. |
| Réactivité | rejette une bougie dès qu'elle est incohérente (prix nul, OHLC impossible, horodatage non croissant, saut > 40 %, trou). |
| Pro-activité | rafraîchit selon une cadence (1 h pour les bougies journalières) sans qu'on le lui demande. |
| Capacité sociale | ses verdicts conditionnent le régime, la volatilité et les stratégies. |

## Paramètres

Saut maximal toléré : 40 % entre deux clôtures ; intervalles : 1m, 5m, 15m, 1h, 4h, 1d.

## Régimes favorables et défavorables

Sans objet (capteur).

## Conditions d'échec

Un vrai krach de plus de 40 % en une bougie serait rejeté à tort (seuil à relever alors). Un trou dans l'historique public rend l'analyse indisponible jusqu'à la prochaine récupération complète. Limite de v1 : il n'y a pas un agent unique ; la validation est mutualisée dans une fonction.

## Résultats mesurés

Tests unitaires : prix nul, OHLC incohérent, saut aberrant, horodatage non croissant, trou, bougie non clôturée.

## Statut dans le cycle de vie

Composant d'infrastructure (hors cycle de vie des stratégies).
