# Audit de l'existant (phase 0, EX-ST-07)

Généré le 2026-10-20 par `orchestre audit-legacy`. Les secrets ne sont jamais recopiés dans ce document.

## Périmètre analysé

- Dossier : `/home/zogning-abel/Desktop/Projet/python/binance`
- Fichiers Python : 1 (main.py)
- Lignes de code : 22
- Classes d'agents détectées : aucune
- Bibliothèques notables : binance

> **Ce dossier ne contient pas le système multi-agents décrit dans la spécification** (§1.2) : peu de fichiers et aucune classe d'agent. Le code de l'ancien système doit être copié dans `legacy/` (sans clé), puis l'audit relancé. Cette absence est la question ouverte Q-2.

## Constats

| Gravité | Nombre |
|---|---|
| CRITIQUE | 2 |
| ÉLEVÉ | 2 |
| MOYEN | 3 |
| INFO | 0 |

| Gravité | Constat | Fichier:ligne | Détail |
|---|---|---|---|
| CRITIQUE | clé en clair | `main.py:4` | valeur littérale affectée à `api_key` (non recopiée ici) : à révoquer puis à lire depuis l'environnement (EX-SEC-02) |
| CRITIQUE | clé en clair | `main.py:5` | valeur littérale affectée à `api_secret` (non recopiée ici) : à révoquer puis à lire depuis l'environnement (EX-SEC-02) |
| ÉLEVÉ | pas de testnet | `(global):0` | ordres écrits sans testnet détecté : plateforme réelle par défaut (EX-SEC-01) |
| ÉLEVÉ | ordre direct | `main.py:23` | écriture d'ordre directe : devra passer par le RiskAgent puis l'ExecutionAgent (EX-ST-09, EX-SEC-12) |
| MOYEN | aucun stop | `(global):0` | aucun stop de protection détecté (EX-SEC-14) |
| MOYEN | aucun test | `(global):0` | aucun fichier de test détecté (EX-QA-01) |
| MOYEN | aucune gestion du risque | `(global):0` | aucun mot-clé de gestion du risque détecté (plafonds, drawdown, taille de position) |

## Gestion des clés

- Clés en clair dans le code : **OUI, à traiter immédiatement**
- Action recommandée en cas de clé en clair : **révoquer la clé sur Binance**, en créer une nouvelle (sans droit de retrait, avec restriction IP) et la lire depuis l'environnement ; ne jamais la versionner ni la partager.

## Tests

- Fichiers de test : aucun
- Gestion du risque (mots-clés) : non détectée ; stops : non détectés
- Testnet : non détecté ; ordres directs : 1

## À compléter par le porteur (Q-2)

- [ ] Où se trouve le code du système multi-agents existant ? (le copier dans `legacy/`, sans clé)
- [ ] Liste des agents et de leur rôle
- [ ] Stratégies appliquées et leurs paramètres
- [ ] Mode de communication entre agents
- [ ] Montant réel engagé lors des essais, durée, résultats observés (gains, pertes, incidents)
- [ ] Ce qui a « marché » et ce qui a posé problème

## Décision de réutilisation (EX-ST-08)

À remplir par le porteur après l'audit : pour chaque agent, réutiliser via un adaptateur (il repart de l'étape BACKTEST du cycle de vie, §14), réécrire, ou abandonner. Un agent existant ne peut plus écrire directement sur Binance (EX-ST-09).
