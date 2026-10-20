# Carte d'agent — VolatilityAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `volatility` |
| Rôle | Mesurer ATR, volatilité réalisée et prévue, et fournir le facteur de ciblage de volatilité. |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Non |
| Code | `analysis/volatility.py`, `analysis/indicators.py` |
| Tests | `tests/unit/test_analysis.py` |
| Exigences de la spécification | EX-AN-10, EX-AN-11 |

## PEAS

- **Performance** : prévisions de volatilité utiles au dimensionnement des positions.
- **Environnement** : bougies journalières.
- **Actionneurs** : ATR(14), volatilité réalisée 30 j, prévision EWMA, facteur de ciblage.
- **Capteurs** : bougies validées.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | calcule seul à chaque bougie. |
| Réactivité | la prévision EWMA réagit vite aux chocs récents. |
| Pro-activité | fournit le facteur sans qu'on le demande. |
| Capacité sociale | utilisé par S-3 pour dimensionner l'exposition. |

## Paramètres

ATR sur 14 périodes ; fenêtre 30 j ; EWMA λ = 0,94 ; cible de volatilité 40 % annualisée ; facteur = cible / prévision, borné à [0 ; 1] sans levier (exemple : 40 %/80 % → 0,5).

## Régimes favorables et défavorables

Sans objet (capteur).

## Conditions d'échec

Une EWMA sous-estime la volatilité juste avant un choc ; ses valeurs sont annualisées sur 365 jours.

## Résultats mesurés

Test de l'exemple de la spécification (40 %/80 % → 0,5).

## Statut dans le cycle de vie

Composant d'infrastructure.
