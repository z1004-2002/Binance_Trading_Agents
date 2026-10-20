"""S-8 Agents du système existant (§11.9, EX-ST-08, EX-ST-09) : adaptateur qui les fait passer par le RiskAgent et l'ExecutionAgent.

Un agent existant ne dispose que d'une méthode `decide(snapshot) -> list[dict]` qui RENVOIE ses intentions (symbol, side, quantity, reason) ;
il ne peut plus écrire lui-même sur Binance. Avant tout chargement, le fichier est audité : clé en clair, retrait ou ordre direct => refus.
Aucune confiance n'est accordée à ce code : ses erreurs sont contenues, ses intentions validées, ses ordres soumis au risque.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal, InvalidOperation
from pathlib import Path

from orchestre.audit.legacy import scan
from orchestre.config import ConfigError, Env
from orchestre.exchange.filters import Side
from orchestre.execution.service import ExecutionService
from orchestre.risk.engine import RiskDecision, review
from orchestre.risk.models import OrderProposal

BLOCKING = {"clé en clair", "retrait", "ordre direct"}


def load_legacy_agent(path: str | Path, class_name: str):
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"agent existant introuvable : {path}")
    report = scan(path.parent, only={path.name})
    kinds = sorted({f.kind for f in report.findings if f.kind in BLOCKING})
    if kinds:
        raise ConfigError(f"agent existant refusé par l'audit ({', '.join(kinds)}) : le corriger puis relancer `audit-legacy`")
    spec = importlib.util.spec_from_file_location(f"legacy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ConfigError(f"agent existant illisible ({path.name}) : {exc!r}") from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ConfigError(f"classe {class_name} introuvable dans {path.name}")
    instance = cls()
    if not callable(getattr(instance, "decide", None)):
        raise ConfigError(f"{class_name} n'a pas de méthode decide(snapshot)")
    return instance


class LegacyRunner:
    def __init__(
        self, agent, symbols: list[str], svc: ExecutionService, context_for, exchange, agent_id: str,
        base_assets: dict[str, str], budget_fn, env: Env = Env.TESTNET, fee_rate: Decimal = Decimal("0.001"), max_intents: int = 5,
    ) -> None:
        self.agent, self.symbols, self.agent_id, self.base_assets = agent, list(symbols), agent_id, base_assets
        self._svc, self._context_for, self._budget_fn, self._env, self._fee, self._max = svc, context_for, budget_fn, env, fee_rate, max_intents
        self.positions = {s: Decimal("0") for s in symbols}
        self.net_spent = Decimal("0")
        self.seq = 0
        self.suspended = False
        self.rejections: list[RiskDecision] = []
        self._alerts: list[str] = []

    @property
    def positioned(self) -> bool:
        return any(q > 0 for q in self.positions.values())

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts, []
        return alerts

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        return self._budget_fn() - self.net_spent + sum((q * prices[s] for s, q in self.positions.items()), Decimal("0"))

    def export_state(self) -> dict:
        return {"positions": {s: str(q) for s, q in self.positions.items()}, "net_spent": str(self.net_spent), "seq": self.seq}

    def import_state(self, state: dict) -> None:
        self.positions.update({s: Decimal(q) for s, q in state["positions"].items() if s in self.positions})
        self.net_spent, self.seq = Decimal(state["net_spent"]), state["seq"]

    def _execute(self, symbol: str, side: Side, qty: Decimal, price: Decimal, why: str) -> None:
        self.seq += 1
        proposal = OrderProposal(
            agent_id=self.agent_id, symbol=symbol, base_asset=self.base_assets[symbol], side=side, quantity=qty, price=price,
            limit_price=None, env=self._env, est_fee=qty * price * self._fee, reduces_risk=side is Side.SELL,
        )
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return
        report = self._svc.execute(decision.order, f"{self.agent_id}-{symbol}-X{self.seq}")
        if report.status == "FILLED":
            r = report.result
            value = r.filled_qty * r.avg_price
            if side is Side.BUY:
                self.positions[symbol] += r.filled_qty
                self.net_spent += value + r.fee_quote
            else:
                self.positions[symbol] -= r.filled_qty
                self.net_spent -= value - r.fee_quote
            self._alerts.append(f"info : agent existant {side.value} {r.filled_qty} {symbol} — {why[:80]}")
        elif report.status == "FAILURE":
            self._alerts.append(f"ordre de l'agent existant échoué : {report.reason}")

    def _intent(self, raw, prices: dict[str, Decimal]) -> None:
        try:
            symbol, side, qty = raw["symbol"], Side(str(raw["side"]).upper()), Decimal(str(raw["quantity"]))
            if symbol not in self.symbols or qty <= 0 or "limit_price" in raw:
                raise ValueError("symbole non autorisé, quantité invalide, ou ordre limite (non pris en charge en v1)")
            if side is Side.SELL and qty > self.positions[symbol]:
                raise ValueError("vente de plus que la position détenue")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            self._alerts.append(f"intention de l'agent existant ignorée ({exc}) : {str(raw)[:80]}")
            return
        self._execute(symbol, side, qty, prices[symbol], str(raw.get("reason", "")))

    def step(self, now, prices: dict[str, Decimal]) -> None:
        if self.suspended:            # budget nul (régime défavorable) : on ferme tout, aucune nouvelle intention
            for symbol, qty in list(self.positions.items()):
                if qty > 0:
                    self._execute(symbol, Side.SELL, qty, prices[symbol], "budget nul : sortie")
            return
        snapshot = {
            "time": now.isoformat(), "prices": {s: float(p) for s, p in prices.items() if s in self.symbols},
            "budget": float(self._budget_fn()), "positions": {s: float(q) for s, q in self.positions.items()},
        }
        try:
            intents = self.agent.decide(snapshot)
        except Exception as exc:       # le code ancien n'est pas digne de confiance
            self._alerts.append(f"agent existant en erreur, ignoré ce tick : {exc!r}")
            return
        for raw in list(intents or [])[: self._max]:
            self._intent(raw, prices)
