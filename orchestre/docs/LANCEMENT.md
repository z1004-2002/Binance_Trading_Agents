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

Résultat attendu : `318 passed, 5 skipped` (les 5 tests ignorés sont ceux du testnet, en opt-in).

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
