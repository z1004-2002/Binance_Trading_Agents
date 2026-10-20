"""Une position (agent, paire) et son stop de protection posé chez Binance (EX-SEC-14, EX-EX-03).

Tout ordre passe par le RiskAgent puis l'exécuteur. Garde-fous :
- une position n'est jamais laissée sans stop : si le stop est impossible ou refusé, on sort au marché ;
- si le marché saute sous la limite du stop (déclenché mais non exécuté), sortie d'urgence au marché ;
- un stop disparu (annulé hors du programme) est reposé au tick suivant.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.config import Env
from orchestre.exchange.filters import Side
from orchestre.execution.service import ExecutionReport, ExecutionService
from orchestre.risk.engine import RiskDecision, review
from orchestre.risk.models import OrderProposal


class Leg:
    def __init__(
        self,
        agent_id: str,
        symbol: str,
        base_asset: str,
        svc: ExecutionService,
        context_for,
        env: Env = Env.TESTNET,
        fee_rate: Decimal = Decimal("0.001"),
        stop_buffer: Decimal = Decimal("0.005"),
        min_trade_pct: Decimal = Decimal("0.02"),
    ) -> None:
        self.agent_id, self.symbol, self.base_asset = agent_id, symbol, base_asset
        self._svc, self._context_for, self._env = svc, context_for, env
        self._fee_rate, self._buffer, self._min_trade_pct = fee_rate, stop_buffer, min_trade_pct
        self.qty = Decimal("0")
        self.net_spent = Decimal("0")       # achats - ventes (frais inclus) : sert à estimer l'équité de l'agent
        self.wanted_stop: Decimal | None = None
        self.stop_price: Decimal | None = None
        self.stop_order_id: str | None = None
        self.stop_qty = Decimal("0")
        self.seq = 0
        self.rejections: list[RiskDecision] = []
        self._alerts: list[str] = []

    # --- alertes ---
    def alert(self, text: str) -> None:
        self._alerts.append(text)

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts, []
        return alerts

    # --- état ---
    def export_state(self) -> dict:
        def s(v):
            return None if v is None else str(v)

        return {
            "qty": str(self.qty), "net_spent": str(self.net_spent), "wanted_stop": s(self.wanted_stop),
            "stop_price": s(self.stop_price), "stop_order_id": self.stop_order_id,
            "stop_qty": str(self.stop_qty), "seq": self.seq,
        }

    def import_state(self, d: dict) -> None:
        def dec(v):
            return None if v is None else Decimal(v)

        self.qty, self.net_spent = Decimal(d["qty"]), Decimal(d["net_spent"])
        self.wanted_stop, self.stop_price = dec(d["wanted_stop"]), dec(d["stop_price"])
        self.stop_order_id, self.stop_qty, self.seq = d["stop_order_id"], Decimal(d["stop_qty"]), d["seq"]

    # --- exécution ---
    def _id(self, tag: str) -> str:
        self.seq += 1
        return f"{self.agent_id}-{self.symbol}-{tag}{self.seq}"

    def _send(self, proposal: OrderProposal, cid: str) -> ExecutionReport | None:
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return None
        return self._svc.execute(decision.order, cid)

    def _proposal(self, side: Side, qty: Decimal, price: Decimal, **kw) -> OrderProposal:
        return OrderProposal(
            agent_id=self.agent_id, symbol=self.symbol, base_asset=self.base_asset, side=side, quantity=qty,
            price=price, limit_price=kw.pop("limit_price", None), env=self._env,
            est_fee=qty * price * self._fee_rate, **kw,
        )

    def _drop_dust(self) -> None:
        if 0 < self.qty < self._svc.filters(self.symbol).min_qty:
            self.qty = Decimal("0")

    def _buy(self, qty: Decimal, price: Decimal) -> None:
        report = self._send(
            self._proposal(Side.BUY, qty, price, is_target_exposure=True, has_stop=self.wanted_stop is not None),
            self._id("B"),
        )
        if report is None:
            return
        if report.status == "FILLED":
            r = report.result
            self.qty += r.filled_qty
            self.net_spent += r.filled_qty * r.avg_price + r.fee_quote
        elif report.status == "FAILURE":
            self.alert(f"achat {self.symbol} échoué : {report.reason}")

    def _sell(self, qty: Decimal, price: Decimal, emergency: bool = False) -> None:
        report = self._send(self._proposal(Side.SELL, qty, price, reduces_risk=True, emergency=emergency), self._id("S"))
        if report is None:
            return
        if report.status == "FILLED":
            r = report.result
            self.qty -= r.filled_qty
            self.net_spent -= r.filled_qty * r.avg_price - r.fee_quote
            self._drop_dust()
        elif report.status == "FAILURE":
            self.alert(f"vente {self.symbol} échouée : {report.reason}")

    def _clear_stop(self) -> None:
        self.stop_order_id, self.stop_price, self.stop_qty = None, None, Decimal("0")

    def _register_stop_fill(self, result) -> None:
        self.qty -= result.filled_qty
        self.net_spent -= result.filled_qty * result.avg_price - result.fee_quote
        self._drop_dust()
        self._clear_stop()
        if self.qty <= 0:
            self.wanted_stop = None

    def _cancel_stop(self) -> None:
        if self.stop_order_id is None:
            return
        report = self._svc.cancel(self.symbol, self.stop_order_id)
        if report.status == "FILLED" and report.result is not None:
            self._register_stop_fill(report.result)
            self.alert(f"stop de {self.symbol} exécuté pendant son annulation")
        else:
            self._clear_stop()

    def _exit(self, price: Decimal, emergency: bool = False) -> None:
        self._cancel_stop()
        if self.qty > 0:
            self._sell(self.qty, price, emergency)
        if self.qty <= 0:
            self.wanted_stop = None

    def _place_stop(self, price: Decimal) -> None:
        if self.qty <= 0 or self.wanted_stop is None:
            return
        if self.wanted_stop >= price:
            self.alert(f"position non protégée sur {self.symbol} (stop {self.wanted_stop} >= prix {price}) : sortie d'urgence")
            self._exit(price, emergency=True)
            return
        self._cancel_stop()
        if self.qty <= 0:
            return
        limit = self.wanted_stop * (1 - self._buffer)
        report = self._send(
            self._proposal(Side.SELL, self.qty, price, limit_price=limit, stop_price=self.wanted_stop,
                           kind="stop", reduces_risk=True),
            self._id("STP"),
        )
        if report is not None and report.status == "OPEN":
            self.stop_order_id, self.stop_price, self.stop_qty = report.result.client_order_id, self.wanted_stop, self.qty
            return
        self.alert(f"stop refusé sur {self.symbol} : position non protégée, sortie d'urgence")
        self._exit(price, emergency=True)

    def propose_stop(self, stop: Decimal | None) -> None:
        """Les stops sont à cliquet : tant que la position est ouverte, un stop ne peut que monter (abaisser un stop augmente
        le risque, donc exige une validation humaine, C-08). Une position neuve repart d'un stop frais."""
        if stop is None:
            return
        floor = max((x for x in (self.wanted_stop, self.stop_price) if x is not None), default=None)
        if self.qty > 0 and floor is not None and stop < floor:
            return
        self.wanted_stop = stop

    # --- API pour les runners ---
    def ensure_protection(self, price: Decimal) -> None:
        if self.qty <= 0 or self.wanted_stop is None:
            return
        if self.stop_order_id and self.stop_price == self.wanted_stop and self.stop_qty == self.qty:
            return
        self._place_stop(price)

    def set_target(self, desired_qty: Decimal, price: Decimal, equity: Decimal) -> None:
        if desired_qty <= 0:
            if self.qty > 0:
                self._exit(price)
            return
        delta = desired_qty - self.qty
        if abs(delta) * price < self._min_trade_pct * equity:
            return  # seuil anti va-et-vient (EX-EX-03)
        if delta > 0:
            self._buy(delta, price)
        else:
            self._cancel_stop()
            self._sell(-delta, price)

    def sync(self) -> None:
        for report in self._svc.sync(self.symbol, self.agent_id):
            if report.result is not None and report.result.client_order_id == self.stop_order_id:
                self._register_stop_fill(report.result)
                self.alert(f"stop déclenché sur {self.symbol} : position fermée à {report.result.avg_price}")
        if self.stop_order_id and self.stop_order_id not in self._svc.open_order_ids(self.symbol):
            self._clear_stop()
            self.alert(f"stop de {self.symbol} introuvable : il sera reposé")

    def guard(self, price: Decimal) -> None:
        """Le marché est sous la limite du stop : déclenché mais non exécuté -> sortie d'urgence au marché."""
        if self.qty > 0 and self.stop_order_id and self.stop_price is not None:
            if price < self.stop_price * (1 - self._buffer):
                self.alert(f"marché sous la limite du stop sur {self.symbol} ({price}) : sortie d'urgence au marché")
                self._exit(price, emergency=True)

    def shutdown(self, price: Decimal, liquidate: bool) -> None:
        """Dernière vérification avant l'arrêt global : règle les stops exécutés, sort si un stop n'a pas pu s'exécuter
        (marché sous sa limite) et, si la politique de l'agent est de liquider, ferme la position."""
        self.sync()
        self.guard(price)
        if liquidate and self.qty > 0:
            self.alert(f"arrêt global : liquidation de la position {self.symbol}")
            self._exit(price, emergency=True)
