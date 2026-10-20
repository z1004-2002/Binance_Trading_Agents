# Cartes d'agents

Une carte par agent (C-06). Chaque carte relie l'agent aux notions du cours et donne ses paramètres, ses conditions d'échec, ses résultats mesurés et son statut dans le cycle de vie (§14).

| Agent | Rôle en une ligne | Carte |
|---|---|---|
| MarketDataAgent (cohérence des données) | Valider les bougies avant que quiconque les utilise : seules les bougies clôturées et cohé… | [marketdata.md](marketdata.md) |
| RegimeAgent | Classer le marché de référence (BTC) en TENDANCE_HAUSSE, TENDANCE_BAISSE, RANGE ou HAUTE_V… | [regime.md](regime.md) |
| VolatilityAgent | Mesurer ATR, volatilité réalisée et prévue, et fournir le facteur de ciblage de volatilité… | [volatility.md](volatility.md) |
| LiquidityAgent | Estimer l'écart achat/vente et le glissement pour la taille d'ordre, et marquer une paire … | [liquidity.md](liquidity.md) |
| SentimentAgent | Lire l'indice Fear & Greed et classer des actualités ; ne peut que RÉDUIRE le risque (mult… | [sentiment.md](sentiment.md) |
| RiskAgent | Accepter ou rejeter chaque proposition d'ordre selon 16 règles ordonnées ; seul son accord… | [risk.md](risk.md) |
| ExecutionAgent | Seul écrivain sur Binance : traduit les ordres approuvés en ordres arrondis aux filtres, s… | [execution.md](execution.md) |
| SupervisorAgent | Surveiller la santé de l'équipe, appliquer pause / reprise / arrêt d'urgence, faire l'auto… | [supervisor.md](supervisor.md) |
| PortfolioAgent (allocateur) | Répartir le capital plafonné entre stratèges selon le régime (v1) ou le rendement ajusté d… | [portfolio.md](portfolio.md) |
| AssistantAgent (LLM Groq) | Expliquer, résumer, répondre aux questions et proposer ; ne passe JAMAIS d'ordre et ne mod… | [assistant.md](assistant.md) |
| S-1 DCA (achat périodique) | Acheter un montant fixe à intervalle régulier, avec une variante modulée par l'indice Fear… | [s1-dca.md](s1-dca.md) |
| S-2 Grid (grille) | Poser des achats sous le prix et revendre un niveau plus haut après chaque achat, pour cap… | [s2-grid.md](s2-grid.md) |
| S-3 Suivi de tendance | Être investi (acheteur seulement) quand le rendement sur 90 jours est positif et le prix a… | [s3-trend.md](s3-trend.md) |
| S-4 Cassure de canal (Donchian) | Entrer quand la clôture dépasse le plus haut des 20 derniers jours, sortir sous le plus ba… | [s4-breakout.md](s4-breakout.md) |
| S-5 Retour à la moyenne | Acheter un excès baissier (sous la bande de Bollinger basse avec RSI bas) et sortir au ret… | [s5-meanreversion.md](s5-meanreversion.md) |
| S-6 Rotation momentum | Chaque semaine, détenir les 3 cryptos au meilleur rendement sur 21 jours à poids égaux ; t… | [s6-rotation.md](s6-rotation.md) |
| S-7 Rééquilibrage à poids constants | Garder des poids fixes (ex. 50 % BTC / 50 % liquidités) en rééquilibrant quand un poids dé… | [s7-rebalance.md](s7-rebalance.md) |

**Non implémenté** : `UniverseAgent` (choix hebdomadaire des paires négociables, EX-AN-04), instruments avancés (futures, marge, Earn : règles R-17 à R-20 et stratégies S-10 à S-14 sont seulement dans la spécification, désactivées).
