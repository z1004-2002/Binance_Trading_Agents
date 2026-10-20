"""Audit statique de l'existant (EX-ST-07, phase 0) : constats vérifiables, jamais de secret recopié (EX-SEC-08).

L'outil ne remplace pas le jugement du porteur : il liste ce qu'un balayage du code peut établir (clés en clair,
ordres directs, retraits, tests, stops) et laisse en blanc ce que seul le porteur sait (résultats observés en réel).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "site-packages", "bin-env", "dist", "build"}
SEVERITIES = ("CRITIQUE", "ÉLEVÉ", "MOYEN", "INFO")
INTERESTING_LIBS = {
    "binance", "ccxt", "requests", "websocket", "websockets", "asyncio", "pandas", "numpy", "redis", "kafka", "pika",
    "flask", "fastapi", "langchain", "autogen", "openai", "anthropic", "groq", "threading", "multiprocessing", "queue",
}

SECRET_ASSIGN = re.compile(
    r"""(?i)\b(?P<name>[\w]*?(?:api[_-]?key|api[_-]?secret|secret(?:[_-]?key)?|token|password|passwd))\b\s*[:=]\s*["'](?P<value>[^"']{16,})["']"""
)
SECRET_CLIENT = re.compile(r"""Client\(\s*["'](?P<a>[^"']{16,})["']\s*,\s*["'](?P<b>[^"']{16,})["']""")
PLACEHOLDER = re.compile(r"(?i)(your|xxx|changeme|<|\.\.\.|example|placeholder|todo|remplace)")
ORDER_CALL = re.compile(
    r"\b(order_limit_buy|order_limit_sell|order_market_buy|order_market_sell|order_oco_sell|order_oco_buy|create_order|"
    r"create_limit_buy_order|create_limit_sell_order|create_market_buy_order|create_market_sell_order|new_order|"
    r"futures_create_order|create_margin_order)\s*\("
)
WITHDRAW = re.compile(r"(?i)\b(withdraw\w*|universal_transfer\w*|margin_transfer\w*|create_withdrawal)\s*\(|capital/withdraw")  # audit-pattern : motif de DÉTECTION, pas un appel (voir test_security)
TESTNET = re.compile(r"(?i)testnet\s*=\s*True|testnet\.binance\.vision|set_sandbox_mode|demo-api\.binance")
STOP = re.compile(r"(?i)stop_loss|stop_price|stoplosslimit|STOP_LOSS|\boco\b|stoploss|trailing")
RISK = re.compile(r"(?i)max_loss|max_drawdown|drawdown|risk|max_position|position_size|stop_loss|stoploss")
IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", re.MULTILINE)
AGENT_CLASS = re.compile(r"^\s*class\s+(\w*Agent\w*)\b", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    severity: str
    kind: str
    file: str
    line: int
    message: str


@dataclass
class AuditReport:
    root: str
    files: list[str] = field(default_factory=list)
    loc: int = 0
    findings: list[Finding] = field(default_factory=list)
    libs: set[str] = field(default_factory=set)
    agent_classes: list[str] = field(default_factory=list)
    has_tests: bool = False
    uses_testnet: bool = False
    has_stops: bool = False
    has_risk_keywords: bool = False
    order_calls: int = 0


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if not (set(p.relative_to(root).parts[:-1]) & SKIP_DIRS))


def scan(root: str | Path) -> AuditReport:
    root = Path(root)
    report = AuditReport(str(root))
    texts: dict[str, str] = {}
    for path in _python_files(root):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="ignore")
        texts[rel] = text
        report.files.append(rel)
        report.loc += sum(1 for line in text.splitlines() if line.strip())
        if path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in path.relative_to(root).parts[:-1]:
            report.has_tests = True
        report.libs |= {m for m in IMPORT.findall(text) if m in INTERESTING_LIBS}
        report.agent_classes += AGENT_CLASS.findall(text)
        for number, line in enumerate(text.splitlines(), 1):
            _scan_line(report, rel, number, line)

    all_text = "\n".join(texts.values())
    report.uses_testnet = bool(TESTNET.search(all_text))
    report.has_stops = bool(STOP.search(all_text))
    report.has_risk_keywords = bool(RISK.search(all_text))
    _global_findings(report)
    return report


def _scan_line(report: AuditReport, rel: str, number: int, line: str) -> None:
    for m in SECRET_ASSIGN.finditer(line):
        if not PLACEHOLDER.search(m.group("value")) and not re.fullmatch(r"(.)\1+", m.group("value")):
            report.findings.append(Finding(
                "CRITIQUE", "clé en clair", rel, number,
                f"valeur littérale affectée à `{m.group('name')}` (non recopiée ici) : à révoquer puis à lire depuis l'environnement (EX-SEC-02)",
            ))
    if SECRET_CLIENT.search(line) and not PLACEHOLDER.search(line):
        report.findings.append(Finding(
            "CRITIQUE", "clé en clair", rel, number,
            "clé et secret littéraux passés au constructeur du client (non recopiés ici) : à révoquer (EX-SEC-02)",
        ))
    if ORDER_CALL.search(line):
        report.order_calls += 1
        report.findings.append(Finding(
            "ÉLEVÉ", "ordre direct", rel, number,
            "écriture d'ordre directe : devra passer par le RiskAgent puis l'ExecutionAgent (EX-ST-09, EX-SEC-12)",
        ))
    if WITHDRAW.search(line):
        report.findings.append(Finding(
            "CRITIQUE", "retrait", rel, number, "appel de retrait ou de transfert : interdit dans ORCHESTRE (EX-SEC-06)",
        ))


def _global_findings(report: AuditReport) -> None:
    add = report.findings.append
    if report.order_calls and not report.uses_testnet:
        add(Finding("ÉLEVÉ", "pas de testnet", "(global)", 0, "ordres écrits sans testnet détecté : plateforme réelle par défaut (EX-SEC-01)"))
    if report.order_calls and not report.has_stops:
        add(Finding("MOYEN", "aucun stop", "(global)", 0, "aucun stop de protection détecté (EX-SEC-14)"))
    if report.files and not report.has_tests:
        add(Finding("MOYEN", "aucun test", "(global)", 0, "aucun fichier de test détecté (EX-QA-01)"))
    if report.files and not report.has_risk_keywords:
        add(Finding("MOYEN", "aucune gestion du risque", "(global)", 0, "aucun mot-clé de gestion du risque détecté (plafonds, drawdown, taille de position)"))


def render_markdown(report: AuditReport, now: datetime) -> str:
    counts = {s: sum(f.severity == s for f in report.findings) for s in SEVERITIES}
    small = len(report.files) < 3 or not report.agent_classes
    lines = [
        "# Audit de l'existant (phase 0, EX-ST-07)", "",
        f"Généré le {now:%Y-%m-%d} par `orchestre audit-legacy`. Les secrets ne sont jamais recopiés dans ce document.", "",
        "## Périmètre analysé", "",
        f"- Dossier : `{report.root}`", f"- Fichiers Python : {len(report.files)} ({', '.join(report.files[:10]) or 'aucun'})",
        f"- Lignes de code : {report.loc}",
        f"- Classes d'agents détectées : {', '.join(report.agent_classes) or 'aucune'}",
        f"- Bibliothèques notables : {', '.join(sorted(report.libs)) or 'aucune'}", "",
    ]
    if small:
        lines += [
            "> **Ce dossier ne contient pas le système multi-agents décrit dans la spécification** (§1.2) : peu de fichiers et "
            "aucune classe d'agent. Le code de l'ancien système doit être copié dans `legacy/` (sans clé), puis l'audit relancé. "
            "Cette absence est la question ouverte Q-2.", "",
        ]
    lines += ["## Constats", "", "| Gravité | Nombre |", "|---|---|"] + [f"| {s} | {counts[s]} |" for s in SEVERITIES] + [""]
    if report.findings:
        lines += ["| Gravité | Constat | Fichier:ligne | Détail |", "|---|---|---|---|"]
        ordered = sorted(report.findings, key=lambda f: (SEVERITIES.index(f.severity), f.file, f.line))
        lines += [f"| {f.severity} | {f.kind} | `{f.file}:{f.line}` | {f.message} |" for f in ordered]
    else:
        lines.append("Aucun constat automatique.")
    critical_secrets = any(f.kind == "clé en clair" for f in report.findings)
    lines += [
        "", "## Gestion des clés", "",
        "- Clés en clair dans le code : **OUI, à traiter immédiatement**" if critical_secrets else "- Clés en clair dans le code : non détectées",
        "- Action recommandée en cas de clé en clair : **révoquer la clé sur Binance**, en créer une nouvelle (sans droit de retrait, "
        "avec restriction IP) et la lire depuis l'environnement ; ne jamais la versionner ni la partager.", "",
        "## Tests", "",
        f"- Fichiers de test : {'présents' if report.has_tests else 'aucun'}",
        f"- Gestion du risque (mots-clés) : {'détectée' if report.has_risk_keywords else 'non détectée'} ; stops : {'détectés' if report.has_stops else 'non détectés'}",
        f"- Testnet : {'utilisé' if report.uses_testnet else 'non détecté'} ; ordres directs : {report.order_calls}", "",
        "## À compléter par le porteur (Q-2)", "",
        "- [ ] Où se trouve le code du système multi-agents existant ? (le copier dans `legacy/`, sans clé)",
        "- [ ] Liste des agents et de leur rôle",
        "- [ ] Stratégies appliquées et leurs paramètres",
        "- [ ] Mode de communication entre agents",
        "- [ ] Montant réel engagé lors des essais, durée, résultats observés (gains, pertes, incidents)",
        "- [ ] Ce qui a « marché » et ce qui a posé problème", "",
        "## Décision de réutilisation (EX-ST-08)", "",
        "À remplir par le porteur après l'audit : pour chaque agent, réutiliser via un adaptateur (il repart de l'étape BACKTEST "
        "du cycle de vie, §14), réécrire, ou abandonner. Un agent existant ne peut plus écrire directement sur Binance (EX-ST-09).", "",
    ]
    return "\n".join(lines)
