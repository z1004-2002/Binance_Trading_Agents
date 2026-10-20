# Carte d'agent — SentimentAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `sentiment` |
| Rôle | Lire l'indice Fear & Greed et classer des actualités ; ne peut que RÉDUIRE le risque (multiplicateur dans [0,5 ; 1] ou pause d'une paire). |
| Type d'agent (Russell & Norvig) | Réflexe fondé sur un modèle |
| Écrit sur Binance ? | Non |
| Code | `analysis/sentiment.py`, `assistant/news.py` |
| Tests | `tests/unit/test_analysis.py`, `tests/unit/test_news_worker.py` |
| Exigences de la spécification | EX-AN-13, EX-AN-14, EX-AN-15 |

## PEAS

- **Performance** : ne jamais augmenter une exposition ; couper vite sur un événement grave.
- **Environnement** : indice Alternative.me (quotidien) ; flux RSS d'actualités.
- **Actionneurs** : multiplicateur d'exposition, valeur transmise au DCA modulé, mise en pause d'une paire.
- **Capteurs** : API Alternative.me, titres RSS classés par l'assistant.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | lit et classe seul, selon sa cadence. |
| Réactivité | une actualité de gravité ≥ 4 (piratage ou régulation, ton ≤ −0,5) met une paire en pause. |
| Pro-activité | relève l'indice toutes les 6 h. |
| Capacité sociale | ses pauses sont appliquées par l'orchestrateur ; seul un humain reprend la paire. |

## Paramètres

Multiplicateur : > 90 ou < 10 → 0,5 ; > 75 ou < 20 → 0,75 ; sinon 1 (propositions à valider). Attribution obligatoire de la source affichée au tableau de bord.

## Régimes favorables et défavorables

Sans objet (capteur).

## Conditions d'échec

L'indice est un indicateur d'humeur, pas un signal prédictif validé ; la classification d'actualités dépend d'un LLM : sorties validées par le code, titres traités comme des données hostiles (injection de prompt).

## Résultats mesurés

Tests : jamais au-dessus de 1, jamais en dessous de 0,5 ; aucune pause créée par une sortie hors schéma.

## Statut dans le cycle de vie

Composant d'infrastructure.
