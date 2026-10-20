# Carte d'agent — RegimeAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `regime` |
| Rôle | Classer le marché de référence (BTC) en TENDANCE_HAUSSE, TENDANCE_BAISSE, RANGE ou HAUTE_VOLATILITE, avec un score de confiance. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle (indicateurs + mémoire de la classification précédente) |
| Écrit sur Binance ? | Non |
| Code | `analysis/regime.py`, `analysis/indicators.py`, `analysis/service.py` |
| Tests | `tests/unit/test_analysis.py`, `tests/unit/test_indicators.py` |
| Exigences de la spécification | EX-AN-05 à EX-AN-09 |

## PEAS

- **Performance** : régimes cohérents avec l'historique, peu d'allers-retours (hystérésis).
- **Environnement** : bougies journalières de BTC (≥ 230 pour décider).
- **Actionneurs** : régime publié et journalisé (table `regimes`), qui module les budgets.
- **Capteurs** : bougies journalières validées.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | classe seul à chaque nouvelle bougie clôturée. |
| Réactivité | un changement de régime n'est publié qu'après 3 lectures consécutives (réactivité tempérée). |
| Pro-activité | rejoue les 10 dernières bougies au démarrage pour initialiser son état. |
| Capacité sociale | son résultat règle le budget de chaque stratège via l'allocateur. |

## Paramètres

ADX(14) > 25 pour une tendance ; moyennes 50 j (pente sur 10 j) et 200 j ; HAUTE_VOLATILITE si la volatilité 30 j dépasse le 90e centile de son année (prioritaire) ; hystérésis 3 bougies ; 230 bougies minimum.

## Régimes favorables et défavorables

Sans objet (capteur).

## Conditions d'échec

Retard inhérent aux moyennes mobiles (le régime change après le mouvement) ; en dessous de 230 bougies (cas du testnet) aucun régime n'est publié et les budgets de base s'appliquent ; seuils non validés par walk-forward.

## Résultats mesurés

Sur 749 jours de BTC : RANGE 56 %, TENDANCE_HAUSSE 24 %, TENDANCE_BAISSE 15 %, HAUTE_VOLATILITE 5 %.

## Statut dans le cycle de vie

Composant d'infrastructure.
