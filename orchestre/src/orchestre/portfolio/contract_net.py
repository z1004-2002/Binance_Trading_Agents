"""Contract Net Protocol (EX-AL-04) [21] sur le bus de messages (performatifs FIPA-ACL, EX-AG-03).

L'allocateur lance un appel d'offres (`cfp`) avec le budget disponible ; chaque stratège répond (`propose`) avec son
budget minimal viable, son historique récent et son multiplicateur de régime ; l'allocateur attribue
(`accept-proposal`) ou refuse (`reject-proposal`, avec la raison) selon l'allocateur v2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from orchestre.core.bus import MessageBus
from orchestre.core.clock import Clock
from orchestre.core.messages import Message, Performative
from orchestre.portfolio.allocator_v2 import AllocatorConfig, target_weights

TTL = timedelta(seconds=60)


class Bidder:
    """Un stratège qui répond aux appels d'offres."""

    def __init__(
        self,
        agent_id: str,
        min_viable: Decimal,
        recent_returns: Callable[[], list[float]],
        regime_multiplier: Callable[[], float],
    ) -> None:
        self.agent_id, self._min, self._returns, self._multiplier = agent_id, min_viable, recent_returns, regime_multiplier
        self._bus: MessageBus | None = None
        self._clock: Clock | None = None

    def attach(self, bus: MessageBus, clock: Clock) -> None:
        self._bus, self._clock = bus, clock
        bus.subscribe("cfp", self._on_cfp)

    def _on_cfp(self, cfp: Message) -> None:
        self._bus.publish(
            Message(
                sender=self.agent_id, topic="propose", performative=Performative.PROPOSE,
                content={"min_viable": str(self._min), "returns": self._returns(), "multiplier": self._multiplier()},
                reason=f"{self.agent_id} propose : minimum viable {self._min}, multiplicateur de régime {self._multiplier()}",
                timestamp=self._clock.now(), ttl=TTL, conversation_id=cfp.conversation_id, in_reply_to=cfp.message_id,
            )
        )


@dataclass
class CnpResult:
    allocations: dict[str, Decimal] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)


class ContractNet:
    def __init__(self, bus: MessageBus, clock: Clock, cfg: AllocatorConfig = AllocatorConfig(), allocator_id: str = "portfolio") -> None:
        self._bus, self._clock, self._cfg, self._id = bus, clock, cfg, allocator_id
        self._inbox: list[Message] = []
        bus.subscribe("propose", self._inbox.append)

    def _message(self, topic, performative, content, reason, cfp=None, reply_to=None) -> Message:
        return Message(
            sender=self._id, topic=topic, performative=performative, content=content, reason=reason,
            timestamp=self._clock.now(), ttl=TTL,
            conversation_id=cfp.conversation_id if cfp else __import__("uuid").uuid4().hex,
            in_reply_to=reply_to,
        )

    def run(self, budget: Decimal, previous: dict[str, float] | None = None) -> CnpResult:
        self._inbox.clear()
        cfp = self._message("cfp", Performative.CFP, {"budget": str(budget)}, f"appel d'offres : budget disponible {budget}")
        self._bus.publish(cfp)
        proposals = {m.sender: m for m in self._inbox if m.conversation_id == cfp.conversation_id}

        remaining = dict(proposals)
        rejected: dict[str, str] = {}
        for agent, p in list(remaining.items()):
            if p.content["multiplier"] <= 0:
                rejected[agent] = "régime défavorable (multiplicateur 0) : budget nul"
                del remaining[agent]
        amounts: dict[str, Decimal] = {}
        while True:
            weights = target_weights(
                {a: p.content["returns"] for a, p in remaining.items()},
                {a: p.content["multiplier"] for a, p in remaining.items()}, previous, self._cfg,
            )
            amounts = {a: (budget * Decimal(str(round(w, 10)))).quantize(Decimal("0.01")) for a, w in weights.items()}
            below = {a: p for a, p in remaining.items() if amounts.get(a, Decimal("0")) < Decimal(p.content["min_viable"])}
            if not below:
                break
            worst = min(below, key=lambda a: amounts.get(a, Decimal("0")))  # un refus à la fois, puis on redistribue
            rejected[worst] = (
                f"budget attribuable {amounts.get(worst, Decimal('0'))} sous le minimum viable {remaining[worst].content['min_viable']}"
            )
            del remaining[worst]
            if not remaining:
                amounts = {}
                break

        for agent, proposal in proposals.items():
            if agent in amounts and agent in remaining:
                self._bus.publish(self._message(
                    "decision", Performative.ACCEPT_PROPOSAL, {"budget": str(amounts[agent])},
                    f"{agent} reçoit {amounts[agent]} sur {budget} (poids ajusté du risque)", cfp, proposal.message_id,
                ))
            else:
                reason = rejected.get(agent, "aucun budget attribuable")
                self._bus.publish(self._message("decision", Performative.REJECT_PROPOSAL, {}, f"{agent} refusé : {reason}", cfp, proposal.message_id))
        return CnpResult({a: v for a, v in amounts.items() if a in remaining}, rejected)
