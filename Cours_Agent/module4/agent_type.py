import pandas as pd
import numpy as np
import ccxt

# --- (a) Agent de reflexe simple ---
class SimpleReflexBot:
    def decide(self, rsi):
        if rsi < 30: return "BUY"
        if rsi > 70: return "SELL"
        return "HOLD"

# --- (b) Agent basé sur un modèle ---
class ModelBasedBot:
    def __init__(self):
        self.recent_prices = []
    
    def update(self, price):
        self.recent_prices.append(price)
        self.recent_prices = self.recent_prices[-20:]
    
    def trend(self):
        if len(self.recent_prices) < 2: return 0
        return self.recent_prices[-1] - self.recent_prices[0]
    
    def decide(self, price,rsi):
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
                self.entry_price= price
                return "BUY"
        else:
            gain = (price - self.entry_price) / self.entry_price
            if gain >= self.target_pct:
                self.entry_price = None
                return "SELL"
        return "HOLD"

# --- (d) Basé sur l'utilité ---
class UtilityBot:
    def __init__(self, risk_aversion=1.0):
        self.risk_aversion = risk_aversion
        
    def utility(self, expected_return, volatility):
        return expected_return - self.risk_aversion * ( volatility**2 )
    
    def decide(self, exp_ret, vol):
        u = self.utility(exp_ret, vol)
        return "BUY" if u > 0 else "HOLD"
    
# --- (e) Apprentissage (bandit €-greedy sur 2 strategy) ---
class LearningBot:
    def __init__(self, epsilon=0.1):
        self.epsilon = epsilon
        self.rewards = {"strategy_A": [], "strategy_B":[]}
        
    def choose(self):
        if np.random.rand() < self.epsilon or not any(self.rewards.values()):
            return np.random.choice(list(self.rewards.keys()))
        avgs = {k: np.mean(v) if v else 0 for k,v in self.rewards.items()}
        return max(avgs, key=avgs.get)
    
    def learn(self, strategy, reward):
        self.rewards[strategy].append(reward)

if __name__ == "__main__":
    #! Création des différents bots
    simple_bot = SimpleReflexBot()
    model_bot = ModelBasedBot()
    goal_bot = GoalBasedBot(target_pct=0.05)
    utility_bot = UtilityBot(risk_aversion=1.0)
    learning_bot = LearningBot(epsilon=0.1)
    
    symbol = 'BTC/USDT'
    timeframe = '1h'
    
    exchange = ccxt.binance()
    
    since_timestamp = exchange.parse8601('2026-07-01T00:00:00Z')
     
    if exchange.has['fetchOHLCV']:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe)
        columns = ['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']
        df = pd.DataFrame(ohlcv, columns=columns)
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
        # print(df.head())
        print(df.tail())
        
        #! calcul du RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
    else:
        print("L'API ne supporte pas la récupération des données OHLCV.")