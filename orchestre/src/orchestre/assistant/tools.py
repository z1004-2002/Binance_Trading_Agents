"""Outils de l'assistant (EX-LLM-01, EX-LLM-04) : lecture seule, plus un unique outil d'écriture limité.

`submit_proposal` dépose une proposition EN ATTENTE ; elle ne change rien tant qu'un humain ne l'a pas décidée (C-08).
Aucun outil d'ordre, de configuration, de clé ou de commande : le modèle ne peut donc pas en réclamer l'exécution.
Le code du projet exécute les outils ; une sortie est toujours bornée et débarrassée de ses secrets (EX-SEC-08).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from orchestre.core.secrets import mask_secrets
from orchestre.dashboard.data import DashboardData
from orchestre.persistence.store import Store

MAX_OUTPUT = 6000
LEVELS = {"INFO", "WARNING", "ERROR", "CRITICAL", "REPORT"}
PROPOSAL_KINDS = {"paramètre", "stratégie", "risque", "autre"}
MAX_PENDING_PROPOSALS = 5


def _fn(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
    }}


class ToolBox:
    def __init__(self, data: DashboardData, store: Store, clock: Callable[[], datetime], monthly_budget: int) -> None:
        self.data, self.store, self._clock, self.monthly_budget = data, store, clock, monthly_budget
        self._handlers = {
            "get_portfolio": self._portfolio, "get_agents": self._agents, "get_recent_trades": self._trades,
            "get_market": self._market, "get_events": self._events, "get_performance": self._performance,
            "get_costs": self._costs, "submit_proposal": self._proposal,
        }

    @property
    def specs(self) -> list[dict]:
        limit = {"limit": {"type": "integer", "description": "nombre maximum de lignes (1 à 100)"}}
        return [
            _fn("get_portfolio", "Capital, valeur de l'équipe, drawdown, palier de risque, régime, comptes des agents."),
            _fn("get_agents", "État, budget, position et dernière décision de chaque agent."),
            _fn("get_recent_trades", "Derniers ordres et exécutions.", limit),
            _fn("get_market", "Régime, volatilité et sentiment par paire."),
            _fn("get_events", "Journal d'événements (alertes, refus du risque).",
                {"level": {"type": "string", "enum": sorted(LEVELS)}, "kind": {"type": "string"}, **limit}),
            _fn("get_performance", "Rendement, drawdown, Sharpe et Sortino de l'équipe et des références."),
            _fn("get_costs", "Jetons consommés par l'assistant ce mois-ci et budget mensuel."),
            _fn("submit_proposal", "Dépose une proposition d'amélioration EN ATTENTE de validation humaine (aucun effet seule).",
                {"kind": {"type": "string", "enum": sorted(PROPOSAL_KINDS)}, "title": {"type": "string"}, "payload": {"type": "object"}},
                ["kind", "title", "payload"]),
        ]

    # --- exécution ---
    def call(self, name: str, raw_args: str) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"erreur": f"outil inconnu ou interdit : {name}"}, ensure_ascii=False)
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("les arguments doivent être un objet JSON")
            return self._fit(handler(args))
        except Exception as exc:
            return json.dumps({"erreur": mask_secrets(str(exc))[:200]}, ensure_ascii=False)

    @staticmethod
    def _fit(result) -> str:
        """Sortie JSON bornée : on réduit les listes jusqu'à tenir dans la limite, et on masque les secrets."""
        while True:
            text = mask_secrets(json.dumps(result, ensure_ascii=False, default=str))
            if len(text) <= MAX_OUTPUT:
                return text
            if isinstance(result, list) and len(result) > 1:
                result = result[: len(result) // 2]
            else:
                return text[: MAX_OUTPUT - 20] + '..."tronqué"'

    @staticmethod
    def _limit(args: dict, default: int = 20) -> int:
        limit = args.get("limit", default)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit doit être un entier entre 1 et 100")
        return limit

    # --- lecture ---
    def _portfolio(self, args: dict):
        info, ledger = self.data.info(), self.data.ledger()
        return {
            "environnement": info.get("env"), "mode": info.get("mode"), "capital_total": ledger["total"],
            "non_alloue": ledger["unallocated"], "valeur_equipe": info.get("equity"), "drawdown": info.get("drawdown"),
            "perte_du_jour": info.get("daily_loss"), "palier": info.get("tier"), "regime": info.get("regime"),
            "kill_switch": info.get("kill"), "comptes": ledger["accounts"],
        }

    def _agents(self, args: dict):
        return [{k: a[k] for k in ("agent_id", "code", "state", "allocated", "committed", "summary")} | {"derniere_decision": (a["last_order"] or {}).get("note")}
                for a in self.data.agents()]

    def _trades(self, args: dict):
        return self.data.orders(self._limit(args, 20))

    def _market(self, args: dict):
        m = self.data.market()
        return {"regimes": m["regimes"], "volatilite": list(m["volatility"].values()), "sentiment": m["sentiment"]}

    def _events(self, args: dict):
        level, kind = args.get("level"), args.get("kind")
        if level is not None and level not in LEVELS:
            raise ValueError(f"level doit être l'un de {sorted(LEVELS)}")
        if kind is not None and not (isinstance(kind, str) and kind.isalnum() and len(kind) <= 20):
            raise ValueError("kind invalide")
        return self.data.events(level=level, kind=kind, limit=self._limit(args, 30))

    def _performance(self, args: dict):
        return self.data.experiment()["metrics"]

    def _costs(self, args: dict):
        return {"tokens_ce_mois": self.store.llm_tokens_for_month(self._clock()), "budget_mensuel": self.monthly_budget}

    # --- seule écriture : une proposition en attente ---
    def _proposal(self, args: dict):
        kind, title, payload = args.get("kind"), args.get("title"), args.get("payload")
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"kind doit être l'un de {sorted(PROPOSAL_KINDS)}")
        if not isinstance(title, str) or not 3 <= len(title) <= 120:
            raise ValueError("title : 3 à 120 caractères")
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 1000:
            raise ValueError("payload : objet JSON de 1000 caractères maximum")
        if len(self.store.list_proposals("pending")) >= MAX_PENDING_PROPOSALS:
            raise ValueError("trop de propositions en attente : attendre la décision du porteur")
        self.store.add_proposal(self._clock(), kind, title, payload)
        return {"statut": "en attente de validation humaine", "titre": title}
