"""Catalogue des agents affiché par le tableau de bord, et schéma des formulaires d'activation.

Les identifiants de stratégie (dca, grid, trend...) sont ceux du moteur. Les entrées non implémentées (S-10 à S-14) sont listées
pour que rien ne semble oublié, avec la raison pour laquelle on ne peut pas les activer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

GROUPS = ["Analyse", "Stratèges", "Décision collective", "Assistant"]


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str                 # sym | symlist | asset | dec | int | weights
    default: str = ""
    optional: bool = False


@dataclass(frozen=True)
class Entry:
    id: str
    code: str
    name: str
    group: str
    summary: str
    implemented: bool = True
    controllable: bool = False        # peut être activé / désactivé depuis l'interface
    paper_only: bool = False
    reason: str = ""                  # pourquoi non implémenté ou limité
    fields: tuple[Field, ...] = field(default_factory=tuple)
    card: str = ""


SYM = Field("symbol", "Paire", "sym", "BTCUSDT")
BUDGET = Field("budget_quote", "Budget (USDT)", "dec", "100")

CATALOG: tuple[Entry, ...] = (
    # --- analyse ---
    Entry("marketdata", "A-1", "MarketDataAgent", "Analyse", "Valide les bougies avant que quiconque les utilise.", card="marketdata.md"),
    Entry("regime", "A-2", "RegimeAgent", "Analyse", "Classe BTC en hausse, baisse, range ou haute volatilité.", card="regime.md"),
    Entry("volatility", "A-3", "VolatilityAgent", "Analyse", "ATR, volatilité réalisée et prévue, ciblage de volatilité.", card="volatility.md"),
    Entry("liquidity", "A-4", "LiquidityAgent", "Analyse", "Écart achat/vente et glissement : marque une paire non liquide.", card="liquidity.md"),
    Entry("sentiment", "A-5", "SentimentAgent", "Analyse", "Fear & Greed et actualités ; ne peut que réduire le risque.", card="sentiment.md"),
    Entry("universe", "A-6", "UniverseAgent", "Analyse", "Choix hebdomadaire des paires négociables.", implemented=False,
          reason="pas encore construit : en attente de la validation de ses paramètres (volume, ancienneté, nombre de paires)."),
    # --- stratèges ---
    Entry("dca", "S-1", "DCA : achat périodique", "Stratèges", "Achète un montant fixe à intervalle régulier.", controllable=True, card="s1-dca.md",
          fields=(SYM, Field("amount_quote", "Montant par achat (USDT)", "dec", "20"), Field("interval_days", "Intervalle (jours)", "int", "7"),
                  Field("cumulative_cap", "Plafond cumulé (USDT)", "dec", "200"))),
    Entry("grid", "S-2", "Grid : grille d'ordres", "Stratèges", "Achète sous le prix et revend un niveau plus haut.", controllable=True, card="s2-grid.md",
          fields=(SYM, Field("lower", "Borne basse", "dec", "55000"), Field("upper", "Borne haute", "dec", "65000"),
                  Field("intervals", "Intervalles", "int", "10"), BUDGET)),
    Entry("trend", "S-3", "Suivi de tendance", "Stratèges", "Investi quand la tendance 90 jours est haussière, stop suiveur.", controllable=True,
          card="s3-trend.md", fields=(SYM, BUDGET)),
    Entry("breakout", "S-4", "Cassure de canal (Donchian)", "Stratèges", "Entre sur un plus haut de 20 jours, sort sous le plus bas de 10.",
          controllable=True, card="s4-breakout.md", fields=(SYM, BUDGET)),
    Entry("meanreversion", "S-5", "Retour à la moyenne", "Stratèges", "Achète un excès baissier, sort au retour à la moyenne.", controllable=True,
          card="s5-meanreversion.md", fields=(SYM, BUDGET)),
    Entry("rotation", "S-6", "Rotation momentum", "Stratèges", "Détient chaque semaine les meilleures cryptos sur 21 jours.", controllable=True,
          card="s6-rotation.md", fields=(Field("symbols", "Paires (séparées par des virgules)", "symlist", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"), BUDGET,
                                         Field("top_k", "Nombre de positions", "int", "3", optional=True))),
    Entry("rebalance", "S-7", "Rééquilibrage à poids constants", "Stratèges", "Garde des poids fixes en rééquilibrant quand ils dérivent.",
          controllable=True, card="s7-rebalance.md", fields=(Field("weights", "Poids (BTCUSDT:0.5,ETHUSDT:0.3)", "weights", "BTCUSDT:0.5,ETHUSDT:0.3"), BUDGET)),
    Entry("legacy", "S-8", "Agents du système existant", "Stratèges", "Adaptateur : fait passer un ancien agent par le risque et l'exécuteur.",
          reason="charge du code : activable par la configuration uniquement (section strategies.legacy), jamais depuis l'interface.", controllable=False),
    Entry("triangular", "S-9a", "Arbitrage triangulaire (observateur)", "Stratèges", "Mesure les écarts entre trois paires ; n'envoie aucun ordre.",
          controllable=True, fields=(Field("a", "Actif A", "asset", "BTC"), Field("b", "Actif B", "asset", "ETH"))),
    Entry("marketmaker", "S-9b", "Tenue de marché (simulation)", "Stratèges", "Cote un achat et une vente autour du prix, simulation seule.",
          controllable=True, paper_only=True, fields=(SYM, BUDGET)),
    Entry("futures_trend", "S-10", "Tendance long/short sur futures", "Stratèges", "", implemented=False,
          reason="instruments avancés (futures) : interface d'exchange, simulateur de liquidation et règles R-17 à R-20 non construits (SPEC §11.11)."),
    Entry("margin", "S-11", "Marge : levier et vente à découvert", "Stratèges", "", implemented=False,
          reason="instruments avancés (marge isolée) : non construits (SPEC §11.11)."),
    Entry("funding", "S-12", "Arbitrage de taux de financement", "Stratèges", "", implemented=False,
          reason="exige spot et perpétuel ensemble : non construit (SPEC §11.11)."),
    Entry("pairs", "S-13", "Pairs trading", "Stratèges", "", implemented=False,
          reason="exige la vente à découvert : non construit, simulation seule en v1 (SPEC §11.11)."),
    Entry("earn", "S-14", "Earn flexible sur la réserve", "Stratèges", "", implemented=False,
          reason="produit Earn : interface d'exchange non construite (SPEC §11.11)."),
    # --- décision collective ---
    Entry("portfolio", "D-1", "PortfolioAgent", "Décision collective", "Répartit le capital entre stratèges selon le régime.", card="portfolio.md"),
    Entry("risk", "D-2", "RiskAgent", "Décision collective", "16 règles : aucun ordre sans son accord.", card="risk.md"),
    Entry("execution", "D-3", "ExecutionAgent", "Décision collective", "Seul écrivain sur Binance.", card="execution.md"),
    Entry("supervisor", "D-4", "SupervisorAgent", "Décision collective", "Santé, pause, arrêt d'urgence, autocontrôle.", card="supervisor.md"),
    # --- assistant ---
    Entry("assistant", "L-1", "AssistantAgent (Groq)", "Assistant", "Explique, résume, propose ; ne passe jamais d'ordre.", card="assistant.md"),
)

BY_ID = {e.id: e for e in CATALOG}

_SYMBOL = re.compile(r"^[A-Z0-9]{5,15}$")
_ASSET = re.compile(r"^[A-Z0-9]{2,10}$")


class FormError(ValueError):
    pass


def _decimal(raw: str, label: str) -> str:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise FormError(f"{label} : nombre invalide") from None
    if not value.is_finite() or value <= 0 or value > Decimal("1e9"):
        raise FormError(f"{label} : doit être un nombre positif raisonnable")
    return str(value)


def _symbol(raw: str, label: str) -> str:
    value = raw.strip().upper()
    if not _SYMBOL.match(value):
        raise FormError(f"{label} : paire invalide (ex. BTCUSDT)")
    return value


def parse_form(entry: Entry, form: dict) -> dict:
    """Transforme les champs du formulaire en paramètres validés. Rien n'est transmis tel quel au moteur."""
    params: dict = {}
    for f in entry.fields:
        raw = (form.get(f.name) or [""])[0]
        if not raw.strip():
            if f.optional:
                continue
            raise FormError(f"{f.label} : champ obligatoire")
        if f.kind == "sym":
            params[f.name] = _symbol(raw, f.label)
        elif f.kind == "asset":
            value = raw.strip().upper()
            if not _ASSET.match(value):
                raise FormError(f"{f.label} : actif invalide (ex. BTC)")
            params[f.name] = value
        elif f.kind == "dec":
            params[f.name] = _decimal(raw, f.label)
        elif f.kind == "int":
            if not raw.strip().isdigit() or not 1 <= int(raw) <= 1000:
                raise FormError(f"{f.label} : entier entre 1 et 1000")
            params[f.name] = int(raw)
        elif f.kind == "symlist":
            symbols = [_symbol(x, f.label) for x in raw.split(",") if x.strip()]
            if not 1 <= len(symbols) <= 20 or len(set(symbols)) != len(symbols):
                raise FormError(f"{f.label} : entre 1 et 20 paires distinctes")
            params[f.name] = symbols
        elif f.kind == "weights":
            weights: dict[str, str] = {}
            for part in raw.split(","):
                symbol, _, weight = part.partition(":")
                weights[_symbol(symbol, f.label)] = _decimal(weight, f.label)
            if sum(Decimal(w) for w in weights.values()) > 1:
                raise FormError(f"{f.label} : la somme des poids dépasse 1")
            params[f.name] = weights
        else:
            raise FormError(f"{f.label} : type inconnu")
    return params


def enable_target(entry: Entry, form: dict) -> str:
    return f"{entry.id}|{json.dumps(parse_form(entry, form), sort_keys=True)}"
