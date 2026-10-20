"""Registre interne par agent (EX-AL-01, EX-SEC-11). Invariant : la somme des parts égale le total."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class LedgerError(ValueError):
    pass


@dataclass
class Account:
    agent_id: str
    allocated: Decimal = Decimal("0")
    committed: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.allocated - self.committed


def _positive(amount: Decimal) -> Decimal:
    if amount <= 0:
        raise LedgerError("le montant doit être > 0")
    return amount


class Ledger:
    def __init__(self, total_capital_quote: Decimal) -> None:
        if total_capital_quote < 0:
            raise LedgerError("capital total négatif")
        self._total = total_capital_quote
        self._accounts: dict[str, Account] = {}

    def snapshot(self) -> dict:
        return {
            "total": str(self._total),
            "accounts": {
                a.agent_id: {"allocated": str(a.allocated), "committed": str(a.committed)}
                for a in self._accounts.values()
            },
        }

    @classmethod
    def from_snapshot(cls, snap: dict) -> "Ledger":
        ledger = cls(Decimal(snap["total"]))
        for aid, acc in snap["accounts"].items():
            ledger._accounts[aid] = Account(aid, Decimal(acc["allocated"]), Decimal(acc["committed"]))
        if ledger.unallocated < 0:
            raise LedgerError("snapshot incohérent : allocations supérieures au total")
        return ledger

    @property
    def total(self) -> Decimal:
        return self._total

    @property
    def unallocated(self) -> Decimal:
        return self._total - sum((a.allocated for a in self._accounts.values()), Decimal("0"))

    def account(self, agent_id: str) -> Account:
        return self._accounts.setdefault(agent_id, Account(agent_id))

    def accounts(self) -> list[Account]:
        return list(self._accounts.values())

    def allocate(self, agent_id: str, amount: Decimal) -> None:
        if _positive(amount) > self.unallocated:
            raise LedgerError("allocation supérieure au capital non alloué")
        self.account(agent_id).allocated += amount

    def deallocate(self, agent_id: str, amount: Decimal) -> None:
        acc = self.account(agent_id)
        if _positive(amount) > acc.available:
            raise LedgerError("désallocation supérieure au disponible de l'agent")
        acc.allocated -= amount

    def commit(self, agent_id: str, amount: Decimal) -> None:
        acc = self.account(agent_id)
        if _positive(amount) > acc.available:
            raise LedgerError("engagement supérieur au budget disponible")
        acc.committed += amount

    def release(self, agent_id: str, amount: Decimal) -> None:
        acc = self.account(agent_id)
        if _positive(amount) > acc.committed:
            raise LedgerError("libération supérieure au montant engagé")
        acc.committed -= amount
