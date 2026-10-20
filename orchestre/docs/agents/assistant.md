# Carte d'agent — AssistantAgent (LLM Groq)

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `assistant` |
| Rôle | Expliquer, résumer, répondre aux questions et proposer ; ne passe JAMAIS d'ordre et ne modifie rien. |
| Type d'agent (Russell & Norvig) | Agent conversationnel |
| Écrit sur Binance ? | Non, jamais |
| Code | `assistant/` (client Groq, outils, agent, worker, actualités) |
| Tests | `tests/unit/test_assistant.py`, `tests/unit/test_news_worker.py` |
| Exigences de la spécification | EX-LLM-01 à EX-LLM-10 |

## PEAS

- **Performance** : réponses exactes et utiles ; zéro action de trading ; coût borné.
- **Environnement** : l'état de l'équipe (base en lecture), la question du porteur.
- **Actionneurs** : réponses, rapport quotidien, propositions EN ATTENTE.
- **Capteurs** : outils en lecture seule (portefeuille, agents, ordres, marché, événements, performance, coûts).

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | répond seul aux questions de la file. |
| Réactivité | n'a aucun effet sur le trading ; hors de la boucle critique. |
| Pro-activité | le worker envoie un rapport quotidien à l'heure choisie. |
| Capacité sociale | dépose des propositions que le porteur accepte ou refuse dans le tableau de bord. |

## Paramètres

Fournisseur Groq (modèle choisi dans ta console, variable `GROQ_MODEL`) ; budget mensuel de jetons (200 000 par défaut), avertissement à 80 % puis coupure ; au plus 4 tours d'outils ; au plus 5 propositions en attente.

## Régimes favorables et défavorables

Sans objet.

## Conditions d'échec

Panne ou budget épuisé : le rapport repasse en version déterministe sans LLM. Un LLM peut se tromper : les chiffres viennent des outils, jamais d'une estimation du modèle. Aucun appel réel à Groq n'a encore été testé (pas de clé fournie).

## Résultats mesurés

Tests avec un faux client : outils refusés hors liste, résultats traités comme données, secrets masqués, boucle bornée, budget respecté.

## Statut dans le cycle de vie

Composant d'infrastructure (optionnel).
