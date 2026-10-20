# Cours : Fondamentaux des Agents

**Objectif :** maîtriser les concepts classiques d'agent (définitions, architectures, environnements, boucle de contrôle, systèmes multi-agents) avant d'aborder l'IA agentique moderne (LLMs, LangChain, AutoGen…).

**Public :** développeur Python débutant/intermédiaire souhaitant construire des agents autonomes de trading sur Binance.

**Durée estimée :** 8 semaines à ~10 h/semaine (1 module tous les 5–7 jours).

**Prérequis Python :** classes, fonctions, `pip`, un peu de `pandas`. Le reste s'apprend en chemin.

**Installation initiale :**

```bash
pip install ccxt pandas numpy python-dotenv pytest
```

> ⚠️ **Règle de sécurité absolue** : dans TOUT ce cours, on utilise soit des données historiques, soit le **testnet Binance** (`https://testnet.binance.vision`). Aucun ordre réel avec du vrai capital avant le Module 10 et avant d'avoir passé les tests unitaires du Module 9.

---

# Module 1 — Qu'est-ce qu'un agent ?

## 1.1 Théorie

Un **agent** est une entité qui :

1. **perçoit** son environnement via des **capteurs** (sensors),
2. **agit** sur cet environnement via des **actionneurs** (actuators),
3. le fait de manière **autonome**, orientée vers un **but** [1].

Wooldridge & Jennings (1995) affinent la définition en exigeant quatre propriétés simultanées [2] :


| Propriété           | Signification                               | Exemple finance                      |
| --------------------- | ------------------------------------------- | ------------------------------------ |
| **Autonomie**         | Agit sans intervention humaine directe      | Le bot passe l'ordre seul            |
| **Réactivité**      | Répond aux changements de l'environnement  | Coupe la position sur flash-crash    |
| **Pro-activité**     | Prend des initiatives pour atteindre un but | Cherche activement les opportunités |
| **Capacité sociale** | Interagit avec d'autres agents              | Coordonne avec un agent risk-manager |

## 1.2 Agent vs script vs objet

- **Script** : suit une séquence fixe, se termine. `download_prices.py` n'est pas un agent.
- **Objet** : encapsule état + méthodes, mais on l'*appelle*. Il ne décide rien seul.
- **Agent** : possède son propre fil d'exécution et décide *quand* agir [2].

## 1.3 Exemple financier concret

Un `PriceWatcherAgent` qui surveille BTC/USDT en boucle et signale des seuils : c'est le degré zéro de l'agent (réactif, autonome, sans pro-activité ni socialité).

## 1.4 Code Python — Premier agent

```python
# module1_price_watcher.py
import ccxt
import time

class PriceWatcherAgent:
    """Agent réactif minimal : surveille un prix et émet un signal."""

    def __init__(self, symbol="BTC/USDT", buy_threshold=60000, sell_threshold=70000):
        self.exchange = ccxt.binance()  # accès public, aucune clé requise
        self.symbol = symbol
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.running = False

    # --- Capteur ---
    def perceive(self):
        ticker = self.exchange.fetch_ticker(self.symbol)
        return ticker["last"]

    # --- Décision ---
    def decide(self, price):
        if price <= self.buy_threshold:
            return "BUY"
        elif price >= self.sell_threshold:
            return "SELL"
        return "HOLD"

    # --- Actionneur (ici : simple log) ---
    def act(self, signal, price):
        print(f"[{time.strftime('%H:%M:%S')}] {self.symbol} = {price} → {signal}")

    # --- Boucle de vie ---
    def run(self, interval=5):
        self.running = True
        while self.running:
            price = self.perceive()
            signal = self.decide(price)
            self.act(signal, price)
            time.sleep(interval)


if __name__ == "__main__":
    agent = PriceWatcherAgent()
    agent.run()
```

## 1.5 Exercices

1. Modifie l'agent pour surveiller **3 paires** simultanément (BTC, ETH, SOL).
2. Ajoute une **hystérésis** : ne pas ré-émettre le même signal deux fois de suite.
3. Justifie par écrit : cet agent est-il pro-actif ? Pourquoi ?

## 1.6 Auto-évaluation

Tu maîtrises ce module si tu peux :

- [ ]  Expliquer la différence entre un script et un agent en 2 phrases
- [ ]  Citer les 4 propriétés de Wooldridge & Jennings sans notes
- [ ]  Faire tourner ton `PriceWatcherAgent` sur données réelles

---

# Module 2 — Le cadre PEAS

## 2.1 Théorie

Avant d'écrire du code, on spécifie l'agent avec **PEAS** [1][6] :

- **P**erformance : comment mesure-t-on le succès ?
- **E**nvironment : dans quel monde vit l'agent ?
- **A**ctuators : quelles actions sont possibles ?
- **S**ensors : quelles observations sont disponibles ?

PEAS est aux agents ce que le cahier des charges est au logiciel classique. Se tromper de PEAS = construire le bon agent pour le mauvais problème.

## 2.2 Exemple financier — fiche PEAS d'un bot spot

```
BOT : trader spot BTC/USDT sur Binance

P (Performance) :
  - PnL net après frais sur 30 jours
  - Drawdown maximal < 15 %
  - Ratio de Sharpe > 1.0
  - Zéro erreur d'API critique

E (Environment) :
  - Marché spot Binance BTC/USDT
  - Volatilité horaire ~1-3 %
  - Frais taker 0.10 %, maker 0.10 %
  - Latence réseau ~50-200 ms

A (Actuators) :
  - create_market_buy_order(symbol, amount)
  - create_market_sell_order(symbol, amount)
  - create_limit_order(symbol, side, amount, price)
  - cancel_order(order_id, symbol)

S (Sensors) :
  - fetch_ticker() → prix courant
  - fetch_ohlcv(timeframe) → bougies
  - fetch_balance() → portefeuille
  - fetch_order_book() → carnet
```

## 2.3 Code Python — Formaliser le PEAS

```python
# module2_peas.py
from dataclasses import dataclass, field
from typing import List

@dataclass
class PEAS:
    performance: List[str] = field(default_factory=list)
    environment: List[str] = field(default_factory=list)
    actuators: List[str] = field(default_factory=list)
    sensors: List[str] = field(default_factory=list)

    def summary(self):
        print("=== FICHE PEAS ===")
        for cat in ["performance", "environment", "actuators", "sensors"]:
            print(f"\n[{cat.upper()}]")
            for item in getattr(self, cat):
                print(f"  - {item}")


spot_bot = PEAS(
    performance=["PnL net > 0", "Drawdown < 15%", "Sharpe > 1.0"],
    environment=["Binance spot", "BTC/USDT", "Frais 0.1%"],
    actuators=["buy_market", "sell_market", "limit_order", "cancel"],
    sensors=["ticker", "ohlcv", "balance", "order_book"],
)
spot_bot.summary()
```

## 2.4 Exercices

1. Rédige la fiche PEAS d'un bot de **grid trading** (achats/ventes échelonnés).
2. Rédige la fiche PEAS d'un bot de **DCA** (Dollar Cost Averaging).
3. Compare : quelle catégorie change le plus entre les deux ?

## 2.5 Auto-évaluation

- [ ]  Je peux rédiger un PEAS complet en 10 minutes pour n'importe quelle stratégie
- [ ]  J'identifie les capteurs manquants avant d'écrire le code

---

# Module 3 — Propriétés des environnements

## 3.1 Théorie — les 6 dimensions

Russell & Norvig classent les environnements sur 6 axes [1][7] :


| Dimension      | Options                      | Impact                                         |
| -------------- | ---------------------------- | ---------------------------------------------- |
| Observabilité | Total / Partiel              | Faut-il un état interne ?                     |
| Déterminisme  | Déterministe / Stochastique | Faut-il des probabilités ?                    |
| Épisodicité  | Épisodique / Séquentiel    | Une décision impacte-t-elle les suivantes ?   |
| Dynamique      | Statique / Dynamique         | L'env. change-t-il pendant qu'on réfléchit ? |
| Discrétion    | Discret / Continu            | Nb d'états et d'actions                       |
| Agents         | Mono / Multi                 | Faut-il modéliser les autres ?                |

## 3.2 Exemple financier — le marché crypto

Le marché Binance BTC/USDT est :

- **Partiellement observable** (ordres cachés, iceberg orders, dark pools)
- **Stochastique** (news, whales, macro imprévisibles)
- **Séquentiel** (chaque trade modifie ton solde et donc tes options futures)
- **Dynamique** (le prix bouge pendant que ton algo calcule)
- **Continu** (prix et volumes)
- **Multi-agents** (market makers, autres bots, humains)

**Conséquence directe** : un simple agent réflexe est *inadapté*. Il faut au minimum un modèle interne + gestion probabiliste [1].

## 3.3 Code Python — Caractériser un environnement

```python
# module3_env.py
from dataclasses import dataclass

@dataclass
class EnvProperties:
    observable: str        # "full" ou "partial"
    determinism: str       # "deterministic" ou "stochastic"
    episodic: bool
    static: bool
    discrete: bool
    single_agent: bool

    def recommend_architecture(self):
        if self.observable == "partial" or not self.static:
            return "Model-based ou hybride (état interne requis)"
        if self.determinism == "stochastic":
            return "Utility-based (arbitrage risque/rendement)"
        return "Simple reflex peut suffire"


crypto = EnvProperties(
    observable="partial",
    determinism="stochastic",
    episodic=False,
    static=False,
    discrete=False,
    single_agent=False,
)
print(crypto.recommend_architecture())
```

## 3.4 Exercices

1. Caractérise l'environnement d'un bot d'**arbitrage** entre 2 exchanges. Qu'est-ce qui change ?
2. Un backtest est-il un environnement statique ou dynamique ? Pourquoi le confondre avec le live est dangereux ?

## 3.5 Auto-évaluation

- [ ]  Je peux classer n'importe quel marché sur les 6 axes
- [ ]  Je comprends pourquoi un backtest ne prouve pas qu'un agent marchera en live

---

# Module 4 — Les 5 types d'agents

## 4.1 Théorie

Russell & Norvig définissent 5 architectures, chacune ajoutant une capacité [1][8] :

### (a) Agent réflexe simple

Règles `condition → action`. Aucune mémoire.
*Ex :* `si RSI < 30 : buy`.

### (b) Agent réflexe basé sur un modèle

Maintient un **état interne** représentant le monde.
*Ex :* mémoriser la tendance des 20 dernières bougies.

### (c) Agent basé sur des buts

Raisonne sur des **objectifs** futurs.
*Ex :* atteindre +5 % de portefeuille en 24 h → choisir la stratégie la plus adaptée.

### (d) Agent basé sur l'utilité

Arbitre entre buts concurrents via une **fonction d'utilité**.
*Ex :* maximiser `rendement - λ × risque` (Sharpe).

### (e) Agent apprenant

Améliore ses règles via **feedback**.
*Ex :* ajuste ses seuils selon les trades passés (RL, bandits, gradient).

## 4.2 Code Python — les 5 versions du même bot

```python
# module4_agent_types.py
import pandas as pd
import numpy as np

# --- (a) Réflexe simple ---
class SimpleReflexBot:
    def decide(self, rsi):
        if rsi < 30: return "BUY"
        if rsi > 70: return "SELL"
        return "HOLD"

# --- (b) Modèle interne ---
class ModelBasedBot:
    def __init__(self):
        self.recent_prices = []

    def update(self, price):
        self.recent_prices.append(price)
        self.recent_prices = self.recent_prices[-20:]

    def trend(self):
        if len(self.recent_prices) < 2: return 0
        return self.recent_prices[-1] - self.recent_prices[0]

    def decide(self, price, rsi):
        self.update(price)
        if rsi < 30 and self.trend() > 0: return "BUY"
        if rsi > 70 and self.trend() < 0: return "SELL"
        return "HOLD"

# --- (c) Basé sur un but ---
class GoalBasedBot:
    def __init__(self, target_pct=0.05):
        self.entry_price = None
        self.target_pct = target_pct

    def decide(self, price, rsi):
        if self.entry_price is None:
            if rsi < 30:
                self.entry_price = price
                return "BUY"
        else:
            gain = (price - self.entry_price) / self.entry_price
            if gain >= self.target_pct:
                self.entry_price = None
                return "SELL"
        return "HOLD"

# --- (d) Utilité ---
class UtilityBot:
    def __init__(self, risk_aversion=2.0):
        self.risk_aversion = risk_aversion

    def utility(self, expected_return, volatility):
        return expected_return - self.risk_aversion * (volatility ** 2)

    def decide(self, exp_ret, vol):
        u = self.utility(exp_ret, vol)
        return "BUY" if u > 0 else "HOLD"

# --- (e) Apprenant (bandit ε-greedy sur 2 stratégies) ---
class LearningBot:
    def __init__(self, epsilon=0.1):
        self.epsilon = epsilon
        self.rewards = {"strategy_A": [], "strategy_B": []}

    def choose(self):
        if np.random.rand() < self.epsilon or not any(self.rewards.values()):
            return np.random.choice(list(self.rewards.keys()))
        avgs = {k: np.mean(v) if v else 0 for k, v in self.rewards.items()}
        return max(avgs, key=avgs.get)

    def learn(self, strategy, reward):
        self.rewards[strategy].append(reward)
```

## 4.3 Exercice fil rouge

Sur les mêmes 6 mois de données OHLCV BTC/USDT (télécharge via `ccxt.fetch_ohlcv`), exécute les 5 agents et compare : PnL, drawdown, nombre de trades. Lequel gagne ? Est-ce reproductible sur d'autres périodes ?

## 4.4 Auto-évaluation

- [ ]  Je nomme les 5 types dans l'ordre croissant de complexité
- [ ]  Je code chacun en < 30 lignes
- [ ]  Je choisis le bon type selon l'environnement

---

# Module 5 — Architectures d'agents

## 5.1 Théorie

Wooldridge (2002) classe les architectures en 4 familles [3][9] :

### Logique

Base de règles + moteur d'inférence. Rigoureux mais lent, peu adapté au temps réel.

### Réactive

**Subsumption architecture** de Brooks : couches simples, pas de représentation symbolique. Rapide, robuste, mais aveugle au long terme.

### BDI (Belief–Desire–Intention)

Introduite par Bratman, Israel & Pollack [9] :

- **Beliefs** : ce que l'agent croit du monde
- **Desires** : ce qu'il veut atteindre
- **Intentions** : ce qu'il a décidé de faire

### Hybride / en couches

Combine une couche réactive (rapide) et une couche délibérative (planifiée). Le meilleur des deux mondes.

## 5.2 Exemple financier — bot hybride

- **Couche réactive** : coupe une position si le prix chute de >5 % en 1 minute (< 100 ms de réaction).
- **Couche délibérative** : rebalance le portefeuille toutes les 4 h selon les corrélations.

## 5.3 Code Python — architecture hybride

```python
# module5_hybrid.py
import threading
import time
import queue

class HybridTradingAgent:
    def __init__(self):
        self.events = queue.Queue()
        self.position = 0
        self.last_price = None
        self.running = False

    # === Couche RÉACTIVE (rapide) ===
    def reactive_loop(self):
        while self.running:
            try:
                event = self.events.get(timeout=0.1)
                if event["type"] == "price":
                    self._react_to_price(event["price"])
            except queue.Empty:
                continue

    def _react_to_price(self, price):
        if self.last_price and self.position > 0:
            drop = (self.last_price - price) / self.last_price
            if drop > 0.05:
                print(f"⚡ FLASH-CRASH détecté : STOP LOSS")
                self.position = 0
        self.last_price = price

    # === Couche DÉLIBÉRATIVE (lente) ===
    def deliberative_loop(self):
        while self.running:
            print("🧠 Analyse portefeuille + rebalancement…")
            # ici : calculs longs, ML, planification
            time.sleep(10)  # simule 4h en démo

    def push_price(self, price):
        self.events.put({"type": "price", "price": price})

    def start(self):
        self.running = True
        threading.Thread(target=self.reactive_loop, daemon=True).start()
        threading.Thread(target=self.deliberative_loop, daemon=True).start()


if __name__ == "__main__":
    agent = HybridTradingAgent()
    agent.start()
    # Simule un flux
    for p in [70000, 70100, 69800, 66000, 65500]:
        agent.push_price(p)
        time.sleep(1)
    time.sleep(15)
```

## 5.4 Exercices

1. Ajoute une **3ème couche** au-dessus : couche stratégique qui décide *quelle stratégie* activer (grid, momentum…).
2. Implémente une version BDI minimale : `beliefs = {"prix": …}`, `desires = {"profit_target": …}`, `intentions = [ordres_planifiés]`.

## 5.5 Auto-évaluation

- [ ]  Je distingue réactif, délibératif, hybride
- [ ]  J'utilise `threading` ou `asyncio` pour séparer les couches

---

# Module 6 — Cycle sense–think–act et rationalité

## 6.1 Théorie

Tout agent tourne dans une boucle [10] :

```
     ┌─────────────┐
  ┌─▶│  PERCEIVE   │◀── environnement
  │  └──────┬──────┘
  │         ▼
  │  ┌─────────────┐
  │  │ UPDATE STATE│
  │  └──────┬──────┘
  │         ▼
  │  ┌─────────────┐
  │  │   DECIDE    │
  │  └──────┬──────┘
  │         ▼
  │  ┌─────────────┐
  └──│    ACT      │──▶ environnement
     └─────────────┘
```

**Rationalité bornée** [1] : un agent est rationnel s'il choisit l'action qui maximise sa performance *espérée*, **compte tenu de ses percepts et de ses ressources**. Il n'est pas irrationnel de rater une info qu'il n'a pas perçue.

## 6.2 Exemple financier

- Un bot n'est pas irrationnel s'il rate un pump imprévisible.
- Il **est** irrationnel s'il ignore un signal RSI qu'il a effectivement lu.
- Il **est** irrationnel s'il calcule pendant 10 s alors que le marché exige 100 ms.

## 6.3 Code Python — classe `Agent` abstraite réutilisable

```python
# module6_base_agent.py
from abc import ABC, abstractmethod
import time
import logging

logging.basicConfig(level=logging.INFO)

class Agent(ABC):
    """Squelette de tout agent : à hériter pour chaque bot."""

    def __init__(self, name):
        self.name = name
        self.state = {}
        self.running = False
        self.log = logging.getLogger(name)

    @abstractmethod
    def perceive(self): ...

    @abstractmethod
    def update_state(self, perception): ...

    @abstractmethod
    def decide(self): ...

    @abstractmethod
    def act(self, decision): ...

    def step(self):
        p = self.perceive()
        self.update_state(p)
        d = self.decide()
        self.act(d)

    def run(self, interval=1.0, max_steps=None):
        self.running = True
        i = 0
        while self.running:
            try:
                self.step()
            except Exception as e:
                self.log.error(f"Step failed: {e}")
            i += 1
            if max_steps and i >= max_steps: break
            time.sleep(interval)
```

Toutes tes futures classes hériteront de `Agent` : garantie d'homogénéité.

## 6.4 Exercices

1. Ré-implémente le `PriceWatcherAgent` du Module 1 comme sous-classe de `Agent`.
2. Ajoute un **timeout** dans `decide()` : si la décision dépasse 200 ms, log un warning (utile pour détecter les régressions).

## 6.5 Auto-évaluation

- [ ]  Tous mes futurs bots héritent d'une classe `Agent` unique
- [ ]  Je sais expliquer pourquoi la rationalité est *bornée*

---

# Module 7 — Systèmes multi-agents (MAS)

## 7.1 Théorie

Pourquoi plusieurs agents plutôt qu'un seul gros [3][11] :

- **Séparation des préoccupations** : chaque agent a une responsabilité claire
- **Parallélisme** : ils travaillent en même temps
- **Robustesse** : si l'un tombe, les autres continuent
- **Spécialisation** : chacun expert dans son domaine

Deux modes d'interaction :

- **Coopératif** : les agents partagent un but commun (ex : maximiser le PnL global)
- **Compétitif** : chaque agent optimise pour lui (ex : trader vs market maker)

## 7.2 Exemple financier — MAS coopératif à 3 agents

- **ScannerAgent** : détecte les opportunités sur 20 paires
- **ExecutorAgent** : passe les ordres
- **RiskAgent** : surveille l'exposition globale et peut annuler tout

## 7.3 Code Python — MAS avec files de messages

```python
# module7_mas.py
import queue
import threading
import time
import random

# Bus de messages partagé
opportunities = queue.Queue()
risk_alerts = queue.Queue()

class ScannerAgent(threading.Thread):
    def run(self):
        while True:
            pair = random.choice(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
            score = random.random()
            if score > 0.8:
                print(f"🔎 Scanner : opportunité {pair} (score={score:.2f})")
                opportunities.put({"pair": pair, "score": score})
            time.sleep(2)

class ExecutorAgent(threading.Thread):
    def run(self):
        while True:
            opp = opportunities.get()
            if not risk_alerts.empty():
                print(f"⛔ Executor : ordre {opp['pair']} bloqué par risk")
                continue
            print(f"✅ Executor : ordre passé sur {opp['pair']}")
            time.sleep(1)

class RiskAgent(threading.Thread):
    def run(self):
        while True:
            time.sleep(10)
            if random.random() > 0.7:
                print("🚨 Risk : exposition trop haute → ALERT")
                risk_alerts.put("STOP")
            else:
                # clear
                while not risk_alerts.empty():
                    risk_alerts.get()

if __name__ == "__main__":
    ScannerAgent(daemon=True).start()
    ExecutorAgent(daemon=True).start()
    RiskAgent(daemon=True).start()
    time.sleep(30)
```

## 7.4 Exercices

1. Remplace `queue.Queue` par une architecture **publish/subscribe** avec `asyncio`.
2. Ajoute un **LoggerAgent** qui écoute tous les messages et écrit dans un CSV.

## 7.5 Auto-évaluation

- [ ]  Je décompose une stratégie complexe en agents spécialisés
- [ ]  Je fais communiquer des agents sans couplage direct

---

# Module 8 — Communication et coordination

## 8.1 Théorie

Historiquement, les agents communiquent via des **actes de langage** : `INFORM`, `REQUEST`, `PROPOSE`, `ACCEPT`, `REJECT` — protocoles FIPA-ACL et KQML [3].

Trois mécanismes de coordination utiles :

- **Négociation** : les agents s'accordent sur une allocation (ex : capital entre stratégies)
- **Enchères** : allocation par système de mise
- **Contrat Net Protocol** : un agent annonce une tâche, d'autres soumissionnent, il choisit

## 8.2 Exemple financier — allocation de capital par enchère

Un `CapitalManager` a 10 000 USDT à allouer. Trois agents-stratégie soumissionnent en promettant un Sharpe attendu. Le manager alloue au plus haut Sharpe pondéré par la confiance.

## 8.3 Code Python — enchère au premier prix

```python
# module8_auction.py
class StrategyAgent:
    def __init__(self, name, expected_sharpe, confidence):
        self.name = name
        self.expected_sharpe = expected_sharpe
        self.confidence = confidence

    def bid(self):
        return self.expected_sharpe * self.confidence

class CapitalManager:
    def __init__(self, capital):
        self.capital = capital

    def allocate(self, strategies):
        bids = [(s, s.bid()) for s in strategies]
        bids.sort(key=lambda x: x[1], reverse=True)
        print("=== Enchères ===")
        for s, b in bids:
            print(f"  {s.name} : bid = {b:.2f}")
        winner = bids[0][0]
        print(f"\n🏆 Gagnant : {winner.name} reçoit {self.capital} USDT")
        return winner

manager = CapitalManager(10000)
strategies = [
    StrategyAgent("Grid", 1.2, 0.7),
    StrategyAgent("Momentum", 1.8, 0.5),
    StrategyAgent("DCA", 0.9, 0.9),
]
manager.allocate(strategies)
```

## 8.4 Exercices

1. Implémente une **enchère Vickrey** (au second prix) et compare les incitations.
2. Ajoute une **négociation itérative** : le manager rejette la meilleure offre si elle dépasse un seuil de risque, et demande une contre-proposition.

## 8.5 Auto-évaluation

- [ ]  Je fais communiquer des agents par messages typés
- [ ]  Je choisis le bon protocole (enchère, négociation) selon le problème

---

# Module 9 — Boîte à outils Python

## 9.1 Bibliothèques essentielles


| Domaine     | Bibliothèque                | Usage                                    |
| ----------- | ---------------------------- | ---------------------------------------- |
| Marchés    | `ccxt`                       | API unifiée pour 100+ exchanges         |
| Marchés    | `python-binance`             | Client officiel Binance (spot + futures) |
| Données    | `pandas`, `numpy`            | Séries temporelles, indicateurs         |
| Concurrence | `asyncio`, `threading`       | Boucles perception/action                |
| Tests       | `pytest`                     | Vérifier le comportement de l'agent     |
| Backtest    | `backtesting.py`, `vectorbt` | Évaluer avant capital réel             |
| Config      | `python-dotenv`              | Isoler les clés API                     |

## 9.2 Squelette d'un projet propre

```
mon_bot/
├── .env                 # BINANCE_API_KEY=…  (jamais commit)
├── .gitignore
├── requirements.txt
├── agents/
│   ├── __init__.py
│   ├── base.py          # classe Agent du Module 6
│   ├── scanner.py
│   ├── executor.py
│   └── risk.py
├── strategies/
│   ├── grid.py
│   └── momentum.py
├── tests/
│   └── test_agents.py
└── main.py
```

## 9.3 Code — connexion sécurisée Binance

```python
# agents/binance_client.py
import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

def get_client(testnet=True):
    client = ccxt.binance({
        "apiKey": os.getenv("BINANCE_API_KEY"),
        "secret": os.getenv("BINANCE_API_SECRET"),
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if testnet:
        client.set_sandbox_mode(True)
    return client
```

## 9.4 Test unitaire — l'agent décide bien

```python
# tests/test_agents.py
from agents.scanner import SimpleReflexBot

def test_buy_on_low_rsi():
    bot = SimpleReflexBot()
    assert bot.decide(rsi=25) == "BUY"

def test_sell_on_high_rsi():
    bot = SimpleReflexBot()
    assert bot.decide(rsi=75) == "SELL"

def test_hold_neutral():
    bot = SimpleReflexBot()
    assert bot.decide(rsi=50) == "HOLD"
```

Lance : `pytest -v`.

## 9.5 Auto-évaluation

- [ ]  Mon projet a une structure propre reproductible
- [ ]  Mes clés API sont dans `.env`, jamais dans le code
- [ ]  Tous mes agents ont au moins 3 tests unitaires

---

# Module 10 — Évaluation et pièges classiques

## 10.1 Comment mesurer un agent

Un agent n'est pas jugé sur *ce qu'il calcule* mais sur *son comportement* dans l'environnement [1] :

- **PnL net** après frais et slippage
- **Drawdown maximal**
- **Ratio de Sharpe** = (rendement - taux sans risque) / volatilité
- **Ratio de Sortino** (ne pénalise que la volatilité baissière)
- **Taux de survie** en live vs backtest

## 10.2 Pièges recensés par Wooldridge & Jennings [5]

1. **Confondre agent et objet** : un objet ne décide pas.
2. **Sur-ingénierie** : ne pas mettre du BDI où un réflexe simple suffit.
3. **Sous-estimer la dynamique** : le marché n'attendra pas ton calcul.
4. **Oublier la coordination** : deux agents non coordonnés peuvent s'annuler.

## 10.3 Pièges spécifiques au trading

- **Look-ahead bias** : utiliser dans le backtest une donnée qui n'existait pas encore à l'instant t
- **Survivorship bias** : ne backtester que sur les tokens encore listés
- **Slippage** ignoré : la vraie exécution ≠ le mid-price
- **Overfitting** : optimiser 50 paramètres sur 6 mois de données
- **Ignorer les frais** : un edge de 0.15 % disparaît avec 0.20 % de frais A/R

## 10.4 Code — mesurer les vraies performances

```python
# module10_metrics.py
import numpy as np

def sharpe_ratio(returns, rf=0.0, periods_per_year=365):
    r = np.array(returns)
    excess = r - rf / periods_per_year
    if excess.std() == 0: return 0
    return np.sqrt(periods_per_year) * excess.mean() / excess.std()

def max_drawdown(equity_curve):
    ec = np.array(equity_curve)
    peaks = np.maximum.accumulate(ec)
    dd = (ec - peaks) / peaks
    return dd.min()

def sortino_ratio(returns, rf=0.0, periods_per_year=365):
    r = np.array(returns)
    excess = r - rf / periods_per_year
    downside = excess[excess < 0].std()
    if downside == 0: return 0
    return np.sqrt(periods_per_year) * excess.mean() / downside
```

## 10.5 Checklist avant capital réel

- [ ]  Backtest sur ≥ 2 ans, incluant un bear market et un bull market
- [ ]  Walk-forward validation (pas juste un split train/test)
- [ ]  Test en paper trading pendant ≥ 30 jours sur testnet
- [ ]  Sharpe > 1.0, drawdown < 20 %, sur les 2 régimes
- [ ]  Tests unitaires verts sur toutes les décisions critiques
- [ ]  Kill-switch manuel opérationnel
- [ ]  Alertes en cas d'erreur d'API

---

# Module 11 — Pont vers l'IA agentique

## 11.1 Ce qui change avec les LLMs

Le module **décision** peut désormais être un **LLM** capable de :

- Raisonner en langage naturel
- Planifier des séquences d'actions (ReAct, Chain-of-Thought)
- Utiliser des outils (function calling)
- Se corriger via une boucle de réflexion [4][10]

Frameworks à connaître : **LangChain**, **LlamaIndex**, **AutoGen**, **CrewAI**, **AutoGPT**.

## 11.2 Ce qui NE change pas

- Le PEAS reste indispensable
- La caractérisation d'environnement reste indispensable
- La boucle sense–think–act reste identique
- La rationalité bornée s'applique encore plus (LLM = coûteux et lent)
- Les pièges (overfitting, look-ahead, slippage) sont les mêmes

## 11.3 Exemple d'agent LLM pour la finance

Un `LLMTradingAgent` où :

- **perceive()** : lit prix, news, tweets
- **decide()** : prompt le LLM avec l'état actuel et une liste d'outils (`fetch_price`, `place_order`, `check_balance`)
- **act()** : exécute la fonction que le LLM a choisie

## 11.4 Prochain cours

Le cours suivant (**IA Agentique**) reprendra ce squelette et remplacera `decide()` par un appel LLM avec function calling, en couvrant :

- Prompt engineering pour agents
- Pattern ReAct (Reason + Act)
- Tool use / function calling
- Memory (short-term, long-term, vector DB)
- Multi-agent avec LLMs (AutoGen, CrewAI)
- Guardrails et safety

---

# Glossaire

- **Agent** : entité autonome qui perçoit et agit dans un environnement
- **BDI** : Belief-Desire-Intention, architecture délibérative
- **Backtest** : simulation d'une stratégie sur données historiques
- **Drawdown** : perte maximale depuis un pic
- **MAS** : Multi-Agent System
- **PEAS** : Performance, Environment, Actuators, Sensors
- **Rationalité bornée** : maximiser l'utilité *espérée* étant donné ses percepts et ressources
- **Sharpe ratio** : rendement excédentaire par unité de volatilité
- **Slippage** : différence entre prix attendu et prix d'exécution
- **Subsumption architecture** : architecture réactive en couches (Brooks)

---

# Références

[1] S. Russell and P. Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed. Hoboken, NJ, USA: Pearson, 2020.

[2] M. Wooldridge and N. R. Jennings, "Intelligent agents: Theory and practice," *The Knowledge Engineering Review*, vol. 10, no. 2, pp. 115–152, 1995.

[3] M. Wooldridge, *An Introduction to MultiAgent Systems*, 2nd ed. Chichester, U.K.: Wiley, 2009.

[4] M. G. Ozsoy, "Intelligent Agents: Exploring Definitions and Bridging Classical and Modern Views," *Medium*, Feb. 2025. [Online]. Available: https://medium.com/@makbule.ozsoy_73232/intelligent-agents-exploring-definitions-and-bridging-classical-and-modern-views-b1a97a1514e2

[5] K. Ko and H. Jin, "What Do We Need for an Agentic Society?," arXiv preprint, 2026. [Online]. Available: https://arxiv.org/pdf/2604.03938

[6] "Understanding PEAS in AI Framework," Scribd. [Online]. Available: https://www.scribd.com/presentation/846852842/PEAS-in-AI

[7] D. Morry, "AI Academy — Artificial Intelligence: A Modern Approach, Chapter 2, Section 3: The Nature of Environments," GitHub repository. [Online]. Available: https://github.com/donaldmorry/02-ai-academy-artificial-intelligence-modern-approach

[8] "Intelligent Agent in AI: Types, Architecture, and Use Cases," *Teachfloor Blog*, Feb. 2026. [Online]. Available: https://www.teachfloor.com/blog/intelligent-agent

[9] "Deliberative agent," *Wikipedia*. [Online]. Available: https://en.wikipedia.org/wiki/Deliberative_agent

[10] Anonymous, "From Question Answering to Task Completion: A Survey on Agent System and Harness Design," arXiv preprint, 2026. [Online]. Available: https://arxiv.org/pdf/2606.20683

[11] "Agentic AI and Multiagentic: Are We Reinventing the Wheel?," arXiv preprint, 2026. [Online]. Available: https://arxiv.org/pdf/2506.01463

[12] ccxt Documentation. [Online]. Available: https://docs.ccxt.com/

[13] Binance Spot Testnet. [Online]. Available: https://testnet.binance.vision/

[x] Contenu pédagogique original rédigé par Claude (Anthropic), synthèse structurée et exemples financiers construits à partir des références [1]–[13], 22 août 2026.
