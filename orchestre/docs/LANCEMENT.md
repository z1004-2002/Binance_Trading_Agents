# Guide de lancement — ORCHESTRE (testnet)

Toutes les commandes se lancent depuis le dossier `orchestre/` :

```bash
cd ~/Desktop/Binance/orchestre
```

`./orchestre.sh` est le lanceur (il fixe le chemin Python et utilise `bin-env`). Les chemins sont relatifs à `orchestre/`.

> **Aujourd'hui, tout se fait sur le TESTNET** (argent virtuel, prix indépendants du marché réel).
> Le mode réel (`env = "live"`) est volontairement bloqué : voir la dernière section.

## 0. Prérequis (une seule fois)

1. Les clés testnet sont dans `orchestre/.env` (`BINANCE_TESTNET_API_KEY` et `BINANCE_TESTNET_API_SECRET`, sans guillemets ni espaces). Ce fichier n'est jamais versionné.
2. Les tests passent :

```bash
../bin-env/bin/python -m pytest -q
```

Résultat attendu : tous les tests passent ; ceux du testnet (opt-in) sont ignorés tant que `ORCHESTRE_TESTNET=1` n'est pas posé.

## 1. Vérifications (aucun ordre n'est envoyé)

```bash
./orchestre.sh check-config --config config/settings.toml
./orchestre.sh check-keys   --config config/settings.toml
./orchestre.sh analyze      --config config/settings.toml
```

- `check-config` affiche le bandeau d'environnement (doit dire `TESTNET`, `dry_run=True`).
- `check-keys` doit répondre `Connexion OK` avec le prix de BTCUSDT.
- `analyze` affiche régime, volatilité, liquidité et sentiment. Sur le testnet, le régime est « indéterminé » (pas assez de bougies journalières) : c'est normal, les budgets de base s'appliquent.

## 2. Répétition en dry-run (calcule tout, n'envoie rien)

`dry_run = true` est la valeur de `config/settings.toml`.

```bash
./orchestre.sh run --config config/settings.toml --db var/dry.db --stop-file var/STOP --ticks 3 --interval 10
./orchestre.sh status --db var/dry.db
```

## 3. Premier vrai passage d'ordres sur le testnet (court)

1. Dans `config/settings.toml`, remplacer `dry_run = true` par `dry_run = false`.
2. **Utiliser une base neuve** : le dry-run a déjà avancé le calendrier du DCA.

```bash
./orchestre.sh run --config config/settings.toml --db var/testnet.db --stop-file var/STOP --ticks 5 --interval 30
./orchestre.sh status --db var/testnet.db
```

Attendu : le DCA achète 20 USDT de BTC (ordre au marché) et le Grid pose des ordres d'achat limites sous le prix.

Nettoyage à la fin d'un essai : annuler tous les ordres ouverts.

```bash
./orchestre.sh cancel-all --config config/settings.toml --symbol BTCUSDT
```

## 4. Lancement long (plusieurs jours)

```bash
nohup ./orchestre.sh run --config config/settings.toml --db var/testnet.db --stop-file var/STOP --interval 30 > var/run.log 2>&1 &
```

Suivre :

```bash
tail -f var/run.log                                   # alertes et événements
./orchestre.sh status --db var/testnet.db             # capital, budgets, ordres suivis
```

Arrêt d'urgence (annule les ordres ouverts et arrête les agents) :

```bash
./orchestre.sh kill --stop-file var/STOP
```

Pour **repartir après un arrêt** (kill switch ou palier de drawdown 15 %) : supprimer le fichier, puis relancer la même commande `run`.

```bash
rm var/STOP
```

## 5. Ce qui se passe tout seul

| Situation | Réaction |
|---|---|
| Drawdown ≥ 5 % | budgets disponibles divisés par deux, alerte |
| Drawdown ≥ 10 % | plus aucun nouvel achat (les ventes de réduction passent) |
| Drawdown ≥ 15 % | arrêt global, `var/STOP` créé, reprise humaine uniquement |
| Prix du Grid sous la borne basse − 2 % | Grid arrêté, ordres annulés (selon `exit_policy`) |
| Écart de réconciliation (ordre inconnu) | équipe en pause, alerte |
| IP bannie (418) | équipe en pause, alerte |
| Données de marché indisponibles > 5 min | stratèges en pause |
| Paire illiquide (écart > 0,3 %) | nouveaux ordres refusés (R-08) |

## 6. Réglages à connaître

- **Plage du Grid** : elle est réglée sur le prix du testnet (~81 670 le 20/10/2026). Si le prix en sort, ajuster `lower` / `upper` dans `config/settings.toml`, puis repartir d'une base neuve (le plafond de capital ne se change pas sur une base existante).
- **Plafond de capital** : `max_total_capital_quote`. Le modifier avec une base existante est refusé (décision humaine requise).
- **Sentiment** : lu sur Alternative.me (nécessite Internet) ; s'il est indisponible, l'équipe fonctionne sans.

## 7. Passage au réel (NON recommandé maintenant)

Le programme refuse de lancer l'argent réel tant que **toutes** ces conditions ne sont pas réunies (spec §6 et §20) :

1. `env = "live"` dans la configuration **et** option `--live` en ligne de commande.
2. Un fichier `docs/decision-live.md` écrit par toi (date, agents, montant, durée).
3. Une clé live **sans droit de retrait ni de transfert, avec restriction IP** (vérifié automatiquement au démarrage).
4. Les clés live dans `.env` (`BINANCE_LIVE_API_KEY`, `BINANCE_LIVE_API_SECRET`), distinctes des clés testnet.

Avant cela, la spec demande au minimum : simulation d'au moins 30 jours sans incident critique, kill switch testé, alertes hors machine opérationnelles, capital réel plafonné à un montant que tu acceptes de perdre entièrement.

## 8. Backtests (données publiques réelles, aucune clé, aucun ordre)

Toutes ces commandes lisent l'historique public de Binance (marché réel) et le mettent en cache dans `data/`.

```bash
# Une paire, journalier : détention, DCA, 50/50, cash, S-3 tendance, S-4 cassure
./orchestre.sh backtest --days 1000
# Une paire, bougies de 4 h : références + S-5 retour à la moyenne
./orchestre.sh backtest --interval 4h --days 700

# Validation glissante (paramètres choisis sur l'entraînement, jugés hors échantillon)
./orchestre.sh walkforward --strategy trend      --days 1000
./orchestre.sh walkforward --strategy breakout   --days 1000
./orchestre.sh walkforward --strategy meanreversion --interval 4h --days 900

# Portefeuille : S-6 rotation et S-7 rééquilibrage ; --null-runs compare S-6 à des rotations au hasard
./orchestre.sh portfolio-backtest --days 1000 --null-runs 200

# Équipe entière : allocateur v1 / v2 + gouvernance par régime, comparée aux références
./orchestre.sh team-backtest --days 1000
```

**Lecture des résultats.** Ces chiffres sont indicatifs : période courte, univers de cryptos existant aujourd'hui (biais du
survivant), et un même historique réutilisé pour plusieurs évaluations (chaque essai augmente le risque de sur-ajustement :
voir la colonne « Essais »). Un backtest n'est pas une promesse de gain ; la vraie validation est la simulation en direct
(spec, phase 9).


## 9. Stratèges à exposition cible (S-3 à S-7) et mode paper

Ces stratèges sont **désactivés par défaut** (`config/strategies.example.toml`) : aucun n'est validé (voir les backtests).
Chaque position qu'ils ouvrent est protégée par un **stop posé chez Binance** (EX-SEC-14) ; l'arrêt d'urgence (`kill`,
palier de drawdown 15 %) **laisse ces stops en place** pour que les positions restent protégées.

**Paper = marché réel en lecture, ordres et soldes simulés** (aucune clé, aucun ordre n'atteint Binance). C'est la façon
sûre de les observer en direct :

```bash
# 1. activer les stratégies voulues dans un fichier de configuration (enabled = true), par ex. config/paper.toml
# 2. lancer la simulation (base séparée : une base n'est jamais partagée entre paper et vraie plateforme)
./orchestre.sh run --paper --config config/paper.toml --db var/paper.db --stop-file var/STOP --interval 60
./orchestre.sh status --db var/paper.db
```

Le capital simulé par défaut est le plafond (`max_total_capital_quote`) ; `--paper-cash 500` pour un autre montant.
Les ordres en attente et les stops simulés sont évalués aux prix relevés à chaque tick (pas intra-bougie).

**À vérifier une fois sur le testnet (test opt-in)** : la pose réelle d'un ordre stop par `ccxt`.

```bash
ORCHESTRE_TESTNET=1 ../bin-env/bin/python -m pytest tests/integration_testnet -v
```

En mode réel, une stratégie à exposition cible est refusée sans `promoted = true` dans sa section (décision humaine, §14).
Limites connues : le stop de protection par défaut de S-6/S-7 (25 %) n'est pas couvert par les backtests ; les stratégies
à état (S-3, S-4, S-5) sauvegardent leur état pour reprendre après un redémarrage sans vendre par erreur.


## 10. Suivi et assistant : alertes, tableau de bord, assistant Groq

**Alertes Telegram (hors machine).** Créer un bot avec @BotFather, puis mettre `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`.
`run` envoie alors les alertes WARNING, ERROR et CRITICAL ainsi que le rapport quotidien. Sans ces variables, rien ne part.

**Tableau de bord local** (lecture seule, 127.0.0.1, aucune clé chargée). Mettre `DASHBOARD_PASSWORD` (8 caractères minimum) dans `.env` :

```bash
./orchestre.sh dashboard --db var/paper.db --stop-file var/STOP --port 8765 --check    # vérifie la configuration
./orchestre.sh dashboard --db var/paper.db --stop-file var/STOP --port 8765            # démarre
```

Ouvrir http://127.0.0.1:8765. À distance : tunnel SSH (`ssh -L 8765:127.0.0.1:8765 serveur`), jamais d'exposition publique.
Pages : vue d'ensemble, agents (pause / reprise), allocation, marché, ordres, risque, expérience, propositions, assistant, journal, aide.
L'arrêt d'urgence exige de taper `STOP`. Les commandes sont appliquées par l'équipe au tick suivant.

**Assistant Groq** (processus séparé : seul à détenir la clé). Dans `.env` : `GROQ_API_KEY`, `GROQ_MODEL` (à choisir dans ta console Groq) et,
si besoin, `GROQ_MONTHLY_TOKEN_BUDGET` :

```bash
./orchestre.sh assistant ask "Pourquoi l'agent trend n'a-t-il pas acheté ?" --db var/paper.db
./orchestre.sh assistant report --db var/paper.db --telegram
./orchestre.sh assistant worker --db var/paper.db            # répond aux questions du tableau de bord, rapport quotidien à 07:00 UTC
./orchestre.sh news --db var/paper.db --rss https://exemple/flux.xml --assets BTC,ETH,SOL
```

L'assistant est en lecture seule : il ne passe aucun ordre. Son seul outil d'écriture dépose une **proposition en attente** que tu acceptes ou refuses dans
le tableau de bord. `news` classe des titres d'actualité (sans aucun outil, sortie validée par le code) et peut mettre une paire en pause (plus aucun nouvel achat ;
les stops et les ventes de réduction restent actifs) ; seul un humain la reprend, depuis la page Marché. Quand le budget de jetons du mois est atteint ou que Groq
est indisponible, l'assistant s'arrête : l'équipe n'en dépend pas, et le rapport quotidien repasse en version sans LLM.

**Audit de l'existant (phase 0).** `./orchestre.sh audit-legacy --path <dossier> --output docs/audit-existant.md` cherche clés en clair, ordres directs, retraits,
absence de tests ou de stops, sans jamais recopier un secret. Le rapport actuel (`docs/audit-existant.md`) porte sur un unique script : le code du système
multi-agents n'est pas sur cette machine.


## 11. Tout lancer d'une commande, expérience, cycle de vie, sauvegarde

**Lancer tout le système** (moteur + tableau de bord + assistant selon `.env`) :

```bash
./start.sh                       # simulation (paper) sur le marché réel, aucune clé Binance nécessaire
./start.sh paper --detach        # idem, en arrière-plan
./start.sh testnet               # ordres sur le testnet (clés testnet requises dans .env)
./orchestre.sh ps                # état de chaque composant (pid, relances, adresse du tableau de bord)
./stop.sh                        # arrêt propre de tout (SIGINT, puis SIGTERM, puis SIGKILL si besoin)
./orchestre.sh up --check        # affiche ce qui serait lancé et ce qui est ignoré, sans rien démarrer
```

Journaux : `var/logs/moteur.log`, `tableau-de-bord.log`, `assistant.log`. Un composant qui plante est relancé avec un délai croissant (5 relances par 10 minutes au plus) ;
une sortie normale (par exemple après un arrêt d'urgence) n'est pas relancée. Le tableau de bord et l'assistant ne reçoivent que leur propre environnement : jamais les clés d'exchange.
**Le mode réel n'est jamais lancé par ces commandes** (il passe par `run --live` et la porte du §20).

**Protocole d'expérience** (à figer AVANT de commencer ; relire d'abord les seuils de `config/experiments/e1-paper.toml`, question ouverte Q-9) :

```bash
./orchestre.sh experiment start  --protocol config/experiments/e1-paper.toml --db var/paper.db
./orchestre.sh experiment status --protocol config/experiments/e1-paper.toml --db var/paper.db   # critères OK / NON / TROP TÔT, règles d'arrêt
```

Modifier le fichier après `start` invalide l'expérience (empreinte SHA-256). Le verdict est indicatif et ne remplace jamais ta décision de passage au réel.

**Cycle de vie des stratégies** (toutes sont à l'étape BACKTEST : aucune n'a passé 30 jours de simulation) :

```bash
./orchestre.sh lifecycle show --db var/paper.db
./orchestre.sh lifecycle set S-3 PAPER --reason "essai en simulation" --db var/paper.db
```

Une promotion avance d'une étape à la fois ; entrer en LIVE_PILOTE ou LIVE exige `--confirm` et la décision écrite `docs/decision-live.md`. Une rétrogradation est libre.

**Sauvegarde** de la base (copie cohérente même pendant que le système tourne) : `./orchestre.sh backup --db var/paper.db --dest backups --keep 14`. Copie-la hors de la machine.
Les modèles de services système et de sauvegarde automatique sont dans `deploy/` (non testés sur un vrai serveur).
