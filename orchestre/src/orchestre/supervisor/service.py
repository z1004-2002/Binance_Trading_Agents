"""SupervisorAgent (cœur) : battements de cœur, pause, kill switch (EX-SU-01, EX-SU-02, EX-SEC-13)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from orchestre.core.agent import Agent, AgentState
from orchestre.core.clock import Clock

COMMANDS = ("pause", "resume", "kill")


class Supervisor:
    def __init__(self, clock: Clock, stop_file: Path, heartbeat_timeout: timedelta) -> None:
        self._clock = clock
        self._stop_file = stop_file
        self._timeout = heartbeat_timeout
        self._agents: dict[str, Agent] = {}
        self._critical: set[str] = set()
        self._last_beat: dict[str, datetime] = {}
        self._killed = False

    def register(self, agent: Agent, critical: bool = False) -> None:
        self._agents[agent.agent_id] = agent
        self._last_beat[agent.agent_id] = self._clock.now()
        if critical:
            self._critical.add(agent.agent_id)

    def heartbeat(self, agent_id: str) -> None:
        self._last_beat[agent_id] = self._clock.now()

    def kill_switch_active(self) -> bool:
        return self._killed or self._stop_file.exists()

    def check_health(self) -> list[str]:
        """Retourne les alertes ; applique pause/dégradation (réductions de risque, C-08)."""
        now = self._clock.now()
        silent = [
            aid
            for aid, beat in self._last_beat.items()
            if now - beat > self._timeout and self._agents[aid].state is AgentState.RUNNING
        ]
        alerts = [f"agent {aid} silencieux depuis plus de {self._timeout}" for aid in silent]
        critical_silent = any(aid in self._critical for aid in silent)
        for aid in silent:
            self._agents[aid].degrade()
        if critical_silent:
            for aid, agent in self._agents.items():
                if aid not in self._critical and agent.state is AgentState.RUNNING:
                    agent.pause()
            alerts.append("agent critique silencieux : stratèges mis en pause")
        return alerts

    def apply_command(self, command: str, target: str | None = None) -> None:
        if command not in COMMANDS:
            raise ValueError(f"commande inconnue: {command!r}")
        if command == "kill":
            self._killed = True
            for agent in self._agents.values():
                agent.stop()
            return
        if self.kill_switch_active():
            raise PermissionError("kill switch actif : reprise humaine explicite requise")
        agent = self._agents[target]
        agent.pause() if command == "pause" else agent.start()
