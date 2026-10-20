"""Contrat d'agent (§9) : percevoir -> décider -> agir, logique de décision pure (A-2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Mapping

from orchestre.core.messages import Message


class AgentState(str, Enum):
    INIT = "INIT"
    WARMUP = "WARMUP"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


class AutonomyLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class Agent(ABC):
    agent_id: str
    role: str
    version: str = "0.1.0"
    agent_type: str
    subscriptions: tuple[str, ...] = ()
    autonomy: AutonomyLevel = AutonomyLevel.L0

    def __init__(self) -> None:
        self.state = AgentState.INIT

    @abstractmethod
    def decide(self, message: Message, snapshot: Mapping[str, Any]) -> list[Message]:
        """Pure : aucun réseau, aucune horloge système, aucun accès base (A-2)."""

    def handle(self, message: Message, snapshot: Mapping[str, Any]) -> list[Message]:
        if self.state is not AgentState.RUNNING:
            return []
        return self.decide(message, snapshot)

    def start(self) -> None:
        self.state = AgentState.RUNNING

    def pause(self) -> None:
        self.state = AgentState.PAUSED

    def degrade(self) -> None:
        self.state = AgentState.DEGRADED

    def stop(self) -> None:
        self.state = AgentState.STOPPED
