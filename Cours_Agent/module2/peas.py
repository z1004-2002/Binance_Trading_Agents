from dataclasses import dataclass, field
from typing import List

@dataclass
class PEAS:
    name: str
    performance: List["str"] = field(default_factory=list)
    environment: List["str"] = field(default_factory=list)
    actuators: List["str"] = field(default_factory=list)
    sensors: List["str"] = field(default_factory=list)
    
    def summary(self):
        print(f"=== FICHE PEAS : {self.name} ===")
        for cat in ["performance", "environment", "actuators", "sensors"]:
            print(f"\n[{cat.upper()}]")
            for item in getattr(self, cat):
                print(f" - {item}")
                
spot_bot = PEAS(
    name="Spot Bot",
    performance=["PnL net > 0", "Drawdown < 15%", "Shape > 1.0"],
    environment=["Binance spot", "BTC/USDT", "Frais 0.1%"],
    actuators=["buy_market", "sell_market","limit_order","cancel"],
    sensors=["ticker","ohlcv","balance","order_book"]
)

# spot_bot.summary()

dca_bot = PEAS(
    name="DCA Bot",
    performance=["PnL 6 mois > 0", "pris moyen d'aquisision < prix moyen sur la periode", "100% des ordres executés","cout total < 0.5%*capital","zerro erreur de montant"],
    environment=["Binance spot", "BTC/USDT", "Frais 0.1%","Episodique"],
    actuators=["create_market_buy_order(symbol, amount_usdt)"],
    sensors=["fetch_balance()","get_current_time()","fetch_ticker(symbol)","fetch_my_trades()"]
)
dca_bot.summary()

grid_bot = PEAS(
    name="Grid Bot",
    performance=["cycles d'achat et vente par jour", "profit > 2*cycle", "maximum drawdown < 15%", "ratio de sharpe > 1.5", "latence < 500ms"],
    environement=["Binance spot", "BTC/USDT", "oscillation des prix sur 1h","frais 0.1%", "volatilité horaire entre 1 et 3%", "latence entre 50 et 200ms"],
    actuators=["create_limit_buy_order","create_limit_sell_order", "cancel_order", "cancel_all_orders", "create_stop_loss_order"],
    sensors=["fetch_ticker()", "fetch_open_orders()" , "fetch_my_trades(since)","fetch_balance()", "fetch_ohlcv('1h',limit=24)", "fetch_order_book()"]
)
grid_bot.summary()  