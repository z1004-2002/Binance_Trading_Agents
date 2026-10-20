import ccxt
import time

from enum import Enum

"""
AGENT D'EMISSION DE DECISION DE VENTE, D'ACHAT OU DE STOP D'UN ACTIF CRYPTOMONNAIE AVEC LES CONTRAINTES CI-DESSOUS
1. Lis les valeurs des (BTC, ETH, SOL)
2. Ne pas reémettre le même ordre
3. ce agent n'est pas pro-actif parce qu'il ne prend pas d'initiative, il n'a pas de but
"""
"""
RESPONSE : 
1. un script est une suite d'instruction et alors qu'un agent décide de quand il agit 
2. propriété d'un agent : autonome, reactif, pro-actif (anticipation, initiative et responsabilité), sociable. 
"""



class decision(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NULL = "NULL"
    

class PriceWatcherAgent:
    """Agent réactif minimal : surveille un prix et émet un signal."""
    
    def __init__(self, symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"], buy_thresholds=[77059.02, 2422, 94.28], sell_thresholds=[77059.1, 2422.5, 94.31]):
        self.exchange = ccxt.binance() # accès public, aucune clé requise
        self.symbols = symbols
        self.buy_thresholds = buy_thresholds
        self.sell_thresholds = sell_thresholds
        self.running = False
        self.last_orders = [decision.NULL, decision.NULL, decision.NULL ]
        
    # --- Capteur ---
    def perceive(self):
        tickers = [self.exchange.fetch_ticker(symbol) for symbol in self.symbols]
        return [ticker["last"] for ticker in tickers]
    
    # --- Décision ---
    def decide(self, prices):
        decisions = []
        for price, buy_threshold, sell_threshold in zip(prices,self.buy_thresholds,self.sell_thresholds):
            if price <= buy_threshold:
                decisions.append(decision.BUY)
            elif price >= sell_threshold:
                decisions.append(decision.SELL)
            else : 
                decisions.append(decision.HOLD)
        return decisions
    
    # --- Actionneur (simple logs) ---
    def act(self, signals, prices):
        print("---------------- nouveaux ordres ----------------")
        for symbol, price, signal, last_order in zip(self.symbols, prices, signals, self.last_orders) : 
            if not (last_order == signal):
                print(f"[{time.strftime('%H:%M:%S')}] {symbol} = {price} -> {signal.value}")
        self.last_orders = [s for s in signals]

    # --- Boucle de vie ---
    def run(self, interval=1):
        self.running = True
        while self.running : 
            prices = self.perceive()
            signals = self.decide(prices=prices)
            self.act(signals=signals, prices=prices)
            time.sleep(interval)
            
if __name__ == "__main__":
    agent = PriceWatcherAgent()
    agent.run()