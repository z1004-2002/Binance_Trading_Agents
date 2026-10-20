# ORCHESTRE

Équipe d'agents de trading autonomes et collaboratifs pour Binance Spot, en Python. **Projet d'apprentissage et d'expérimentation** : il peut perdre de
l'argent, et aucun résultat ici n'est une promesse de gain ni un conseil financier. La spécification est dans `../SPEC.md` (source de vérité).

## Démarrer en 3 commandes
```bash
cd orchestre
../bin-env/bin/python -m pytest -q          # tous les tests doivent passer (ceux du testnet sont ignorés tant qu'on ne les active pas)
./start.sh                                    # simulation (paper) sur le marché réel : moteur + tableau de bord + assistant
./stop.sh                                     # tout arrêter proprement
```
`./start.sh` lit `.env` (copie de `.env.example`) : le tableau de bord démarre si `DASHBOARD_PASSWORD` est défini, l'assistant si `GROQ_API_KEY` et `GROQ_MODEL`
le sont. Le mode paper n'a besoin d'aucune clé Binance et n'envoie aucun ordre réel. `./start.sh testnet` passe de vrais ordres sur le testnet (argent virtuel).
`./start.sh paper --detach` lance en arrière-plan ; `./orchestre.sh ps` montre l'état. **Le script ne lance jamais l'argent réel.**

## Principes de sécurité
- Testnet ou simulation par défaut ; le réel exige une double confirmation, la décision écrite `docs/decision-live.md` et une clé sans droit de retrait.
- Seul l'`ExecutionAgent` écrit sur Binance, et seulement après l'accord du `RiskAgent` ; aucun appel de retrait ou de transfert n'existe dans le code (testé).
- Chaque position à exposition cible est protégée par un stop posé chez Binance (qui survit à l'arrêt) ; les stops ne descendent jamais tant que la position est ouverte.
- Ce qui augmente le risque exige une validation humaine ; ce qui le réduit peut être automatique.
- L'assistant LLM est en lecture seule ; le tableau de bord n'a accès à aucune clé.

## Commandes (`./orchestre.sh <commande>`)
| Besoin | Commande |
|---|---|
| Tout lancer / arrêter / voir l'état | `up`, `down`, `ps` (ou `start.sh`, `stop.sh`) |
| Lancer le moteur seul | `run --paper` ou `run` (testnet) |
| Vérifier configuration et clés | `check-config`, `check-keys`, `analyze` |
| Suivre l'équipe | `status`, `dashboard` |
| Arrêt d'urgence | `kill`, `cancel-all` |
| Comparer des stratégies sur l'historique | `backtest`, `walkforward`, `portfolio-backtest`, `team-backtest` |
| Expérience et cycle de vie | `experiment start/status`, `lifecycle show/set` |
| Assistant | `assistant ask/report/worker`, `news` |
| Entretien | `backup`, `audit-legacy` |

Le détail et les cas d'usage sont dans `docs/LANCEMENT.md`. Les cartes des agents (rôle, PEAS, résultats, conditions d'échec) sont dans `docs/agents/`.

## Où en est le projet (2026-10-20)
- **Fait et testé** : socle d'exchange et d'exécution, RiskAgent, superviseur, allocateur, stratégies S-1 à S-7, agents d'analyse, moteurs de backtest et de validation glissante,
  exécution des stops de protection, paper trading, tableau de bord, alertes Telegram, assistant Groq, lanceur global. Le scénario d'acceptation du krach de −30 % passe.
- **Pas validé** : aucune stratégie n'a passé la simulation de 30 jours ; les backtests sont indicatifs (période courte, biais du survivant) ; S-3, S-4 et S-5 ne sont pas
  concluantes hors échantillon. L'ordre stop réel via ccxt, l'assistant Groq réel et les services système n'ont pas été vérifiés sur de vrais comptes ou serveurs.
- **Pas fait** : audit de l'ancien système (son code n'est pas sur cette machine), instruments avancés (futures, marge, Earn), `UniverseAgent`, flux WebSocket d'exécution.

## Structure
`src/orchestre/` : `exchange/` (Binance, factice, paper), `risk/`, `execution/`, `portfolio/`, `strategies/`, `analysis/`, `runtime/` (orchestrateur), `supervisor/`,
`backtest/`, `metrics/`, `assistant/`, `dashboard/`, `notify/`, `persistence/`, `experiment/`, `cli/`. Tests dans `tests/` ; configuration dans `config/` ; modèles de
déploiement dans `deploy/`.
