"""Vues HTML du tableau de bord (EX-UI-02, EX-UI-05, EX-UI-06). Tout texte venant de la base est échappé."""

from __future__ import annotations

import html
from datetime import datetime

from orchestre.analysis.sentiment import ATTRIBUTION

NAV = [
    ("/", "Vue d'ensemble"), ("/agents", "Agents"), ("/allocation", "Allocation"), ("/market", "Marché"),
    ("/orders", "Ordres"), ("/risk", "Risque"), ("/experiment", "Expérience"), ("/proposals", "Propositions"),
    ("/assistant", "Assistant"), ("/journal", "Journal"), ("/help", "Aide"),
]
TIERS = {
    0: ("ok", "Normal"), 1: ("warn", "Palier 1 (≥ 5 %) : budgets réduits"),
    2: ("warn", "Palier 2 (≥ 10 %) : plus aucun achat"), 3: ("crit", "Palier 3 (≥ 15 %) : arrêt"),
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value) -> str:
    return f"{float(value):,.2f}".replace(",", " ")


def pct(value) -> str:
    return f"{float(value):.1%}"


def badge(kind: str, text: str) -> str:
    symbol = {"ok": "✓", "warn": "!", "crit": "✕", "muted": "·"}[kind]
    return f'<span class="badge {kind}">{symbol} {esc(text)}</span>'


def banner(info: dict) -> tuple[str, str]:
    if not info:
        return "unknown", "ÉTAT INCONNU : l'équipe n'a encore rien écrit"
    if info.get("mode") == "paper":
        return "paper", "PAPER · simulation, aucun ordre réel"
    env = info.get("env")
    if env == "live":
        return "live", "RÉEL · ARGENT RÉEL"
    return "testnet", f"{str(env).upper()} · argent virtuel"


def heartbeat(info: dict) -> tuple[str, str]:
    age = info.get("tick_age")
    if age is None:
        return "muted", "inconnu"
    if age <= 120:
        return "ok", f"{age} s"
    if age <= 600:
        return "warn", f"{age} s"
    return "crit", f"SILENCIEUX ({age} s)"


def layout(title: str, body: str, active: str, info: dict, csrf: str, msg: str = "", autorefresh: bool = False) -> str:
    cls, text = banner(info)
    tier_kind, tier_text = TIERS.get(info.get("tier", 0), TIERS[0])
    hb_kind, hb_text = heartbeat(info)
    kill = "ARRÊT D'URGENCE ACTIF" if info.get("kill") else "Kill switch inactif"
    nav = "".join(f'<a href="{href}"{" class=active" if href == active else ""}>{esc(label)}</a>' for href, label in NAV)
    flash = f'<div class="msg" role="status">{esc(msg[:200])}</div>' if msg else ""
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · ORCHESTRE</title><link rel="stylesheet" href="/static/app.css"></head>
<body{' data-autorefresh="1"' if autorefresh else ''}><header>
<div class="banner {cls}"><span>{esc(text)}</span><span class="chips">
<span class="chip" id="hb">Dernier tick : {esc(hb_text)}</span><span class="chip">{esc(tier_text)}</span><span class="chip">{esc(kill)}</span></span></div>
<nav aria-label="Navigation">{nav}</nav></header>
<main><h1>{esc(title)}</h1>{flash}{body}
<form class="inline mt" method="post" action="/logout"><input type="hidden" name="csrf" value="{esc(csrf)}">
<button>Se déconnecter</button></form></main><script src="/static/app.js"></script></body></html>"""


def login_page(error: str = "") -> str:
    err = f'<div class="msg" role="alert">{esc(error)}</div>' if error else ""
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connexion · ORCHESTRE</title><link rel="stylesheet" href="/static/app.css"></head><body><div class="login">
<h1>ORCHESTRE</h1><p class="muted">Tableau de bord local. Accès réservé à cette machine.</p>{err}
<form method="post" action="/login"><label for="p">Mot de passe</label><input id="p" type="password" name="password" autocomplete="current-password" autofocus required>
<p><button class="primary">Se connecter</button></p></form></div></body></html>"""


def table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{cell}</td>' for i, cell in enumerate(row)) + "</tr>"
        for row in rows
    ) or f'<tr><td colspan="{len(headers)}" class="muted">Aucune donnée.</td></tr>'
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def card(label: str, value: str, sub: str = "") -> str:
    return f'<div class="card"><div class="label">{esc(label)}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'


def line_chart(series: dict[str, list[tuple[str, float]]], width: int = 720, height: int = 260) -> str:
    points = {n: p for n, p in series.items() if len(p) >= 2}
    if not points:
        return '<p class="muted">Pas encore assez de points pour tracer une courbe (un point par heure).</p>'
    epoch = lambda ts: datetime.fromisoformat(ts).timestamp()  # noqa: E731
    xs = [epoch(ts) for p in points.values() for ts, _ in p]
    ys = [v for p in points.values() for _, v in p]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = (y1 - y0) * 0.08 or 1.0
    y0, y1 = y0 - pad, y1 + pad
    left, right, top, bottom = 56, 12, 12, 28
    sx = lambda x: left + (x - x0) / ((x1 - x0) or 1) * (width - left - right)  # noqa: E731
    sy = lambda y: top + (1 - (y - y0) / (y1 - y0)) * (height - top - bottom)  # noqa: E731
    lines, legend = [], []
    for i, (name, pts) in enumerate(points.items()):
        coords = " ".join(f"{sx(epoch(ts)):.1f},{sy(v):.1f}" for ts, v in pts)
        lines.append(f'<polyline class="l{min(i, 1)}" points="{coords}"/>')
        legend.append(f'<span>{"━" if i == 0 else "┅"} {esc(name)} : {money(pts[-1][1])}</span>')
    first = esc(min(ts for p in points.values() for ts, _ in p)[:10])
    last = esc(max(ts for p in points.values() for ts, _ in p)[:10])
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Courbes de valeur">'
        f'<title>Valeur de l\'équipe et références</title>'
        f'<text x="4" y="{top + 8}">{esc(money(y1))}</text><text x="4" y="{height - bottom}">{esc(money(y0))}</text>'
        f'<text x="{left}" y="{height - 8}">{first}</text><text x="{width - right}" y="{height - 8}" text-anchor="end">{last}</text>'
        + "".join(lines) + f'</svg><div class="legend">{"".join(legend)}</div>'
    )


def command_form(csrf: str, command: str, target: str, label: str, css: str = "") -> str:
    return (
        f'<form class="inline" method="post" action="/command"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="command" value="{esc(command)}"><input type="hidden" name="target" value="{esc(target)}">'
        f'<button class="{css}">{esc(label)}</button></form>'
    )


def kill_zone(csrf: str) -> str:
    return f"""<div class="danger-zone"><h2>Arrêt d'urgence</h2>
<p class="small">Annule les ordres ouverts et arrête tous les agents. Les stops de protection posés chez Binance restent en place.
La reprise se fait ensuite à la main (supprimer le fichier STOP et relancer).</p>
<form method="post" action="/command" data-confirm="Activer l'arrêt d'urgence ?"><input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="command" value="kill"><input type="hidden" name="target" value="">
<label for="k">Tape STOP pour confirmer</label><input id="k" type="text" name="confirm" autocomplete="off" class="narrow">
<p><button class="danger">Activer l'arrêt d'urgence</button></p></form></div>"""


# --- pages ---
def overview(data, csrf: str) -> str:
    info, ledger = data.info(), data.ledger()
    exp = data.experiment()
    dd = info.get("drawdown", 0.0)
    dd_kind = "ok" if dd < 0.05 else "warn" if dd < 0.15 else "crit"
    cards = "".join([
        card("Capital plafonné", money(ledger["total"]), f"non alloué : {money(ledger['unallocated'])}"),
        card("Valeur de l'équipe", money(info.get("equity", 0)), "capital + gains/pertes depuis le départ"),
        card("Drawdown", pct(dd), badge(dd_kind, "OK" if dd_kind == "ok" else "attention" if dd_kind == "warn" else "critique")),
        card("Perte du jour", pct(info.get("daily_loss", 0.0)), "pause des stratèges à 3 %"),
        card("Régime de BTC", esc(info.get("regime") or "inconnu"), ""),
        card("Sentiment (Fear & Greed)", esc(info.get("fear_greed") if info.get("fear_greed") is not None else "—"), "Source : Alternative.me"),
    ])
    alerts = table(["Heure (UTC)", "Niveau", "Message"], [[esc(a["ts"][:19]), esc(a["level"]), esc(a["detail"])] for a in data.alerts()])
    chart = line_chart({"TEAM": exp["team"], **exp["refs"]})
    return f'<div class="grid">{cards}</div><h2>Valeur dans le temps</h2>{chart}<h2>Alertes récentes</h2>{alerts}'


def agents(data, csrf: str) -> str:
    rows = []
    for a in data.agents():
        kind = {"RUNNING": "ok", "PAUSED": "warn", "DEGRADED": "crit", "STOPPED": "crit"}.get(a["state"], "muted")
        last = a["last_order"]
        last_text = f'{esc(last["side"])} {esc(last["qty"])} {esc(last["symbol"])} ({esc(last["status"])}) : {esc(last["note"])}' if last else "—"
        actions = command_form(csrf, "pause", a["agent_id"], "Pause") + " " + command_form(csrf, "resume", a["agent_id"], "Reprise")
        rows.append([f'<b>{esc(a["agent_id"])}</b> <span class="muted">{esc(a["code"])}</span>', badge(kind, a["state"]),
                     money(a["allocated"]), money(a["committed"]), esc(a["summary"]), last_text, actions])
    all_actions = command_form(csrf, "pause", "all", "Tout mettre en pause") + " " + command_form(csrf, "resume", "all", "Tout reprendre")
    return (
        table(["Agent", "État", "Alloué", "Engagé", "Position", "Dernière décision", "Actions"], rows, {2, 3})
        + f"<p>{all_actions}</p>" + kill_zone(csrf)
        + "<h2>Commandes récentes</h2>"
        + table(["Heure", "Commande", "Cible", "Statut", "Résultat"], [[esc(c["ts"][:19]), esc(c["command"]), esc(c["target"] or ""), esc(c["status"]), esc(c["result"] or "")] for c in data.commands()])
    )


def allocation(data, csrf: str) -> str:
    a = data.allocation()
    mult = a["multipliers"]
    rows = [
        [esc(acc["agent_id"]), esc(a["codes"].get(acc["agent_id"], "")), money(acc["allocated"]), money(acc["committed"]),
         money(acc["available"]), esc(mult.get(acc["agent_id"], "—"))]
        for acc in a["ledger"]["accounts"]
    ]
    note = f"Régime actuel : <b>{esc(a['regime'] or 'inconnu')}</b>. Le multiplicateur réduit (jamais n'augmente) le budget configuré de chaque stratège."
    return f"<p>{note}</p>" + table(["Agent", "Stratégie", "Alloué", "Engagé", "Disponible", "Multiplicateur de régime"], rows, {2, 3, 4}) + \
        f'<p class="small muted">Capital plafonné {money(a["ledger"]["total"])}, non alloué {money(a["ledger"]["unallocated"])} (réserve de trésorerie incluse).</p>'


def market(data, csrf: str) -> str:
    m = data.market()
    rows = []
    for r in m["regimes"]:
        v = m["volatility"].get(r["symbol"])
        rows.append([esc(r["symbol"]), esc(r["regime"]), pct(r["confidence"]), esc(r["ts"][:10]),
                     pct(v["realized"]) if v else "—", pct(v["forecast"]) if v else "—", money(v["atr"]) if v else "—"])
    s = m["sentiment"]
    senti = f'<p>Indice Fear & Greed : <b>{esc(s["value"])}/100</b> ({esc(s["classification"])}), le {esc(s["ts"][:10])}.</p>' if s else "<p class=muted>Sentiment indisponible.</p>"
    paused = data.info().get("paused_pairs") or {}
    paused_rows = [[esc(p), esc(reason), command_form(csrf, "resume_pair", p, "Reprendre")] for p, reason in sorted(paused.items())]
    paused_html = ("<h2>Paires en pause (actualité grave)</h2>"
                   "<p class=\"small muted\">Aucun nouvel achat n'est possible sur ces paires ; les ventes de réduction et les stops restent actifs. "
                   "Seul un humain peut les reprendre.</p>" + table(["Paire", "Raison", "Action"], paused_rows)) if paused else ""
    return (table(["Paire", "Régime", "Confiance", "Date", "Vol. réalisée", "Vol. prévue", "ATR"], rows, {2, 4, 5, 6})
            + paused_html + f"<h2>Sentiment</h2>{senti}<p class=\"small muted\">{esc(ATTRIBUTION)}</p>")


def orders(data, csrf: str) -> str:
    open_rows = [[esc(o["symbol"]), esc(o["client_order_id"]), esc(o["agent_id"]), esc(o["side"]),
                  badge("ok", "stop de protection") if o["protective"] else badge("muted", "ordre")] for o in data.open_orders()]
    log_rows = [[esc(o["ts"][:19]), esc(o["agent_id"]), esc(o["symbol"]), esc(o["side"]), esc(o["qty"]), esc(o["price"]),
                 esc(o["fee"]), esc(o["status"]), esc(o["client_order_id"]), esc(o["note"])] for o in data.orders()]
    return ("<h2>Ordres ouverts</h2>" + table(["Paire", "Identifiant", "Agent", "Sens", "Type"], open_rows)
            + "<h2>Historique</h2>" + table(["Heure", "Agent", "Paire", "Sens", "Qté", "Prix", "Frais", "Statut", "Identifiant", "Note"], log_rows, {4, 5, 6}))


def risk(data, csrf: str) -> str:
    info = data.info()
    kind, text = TIERS.get(info.get("tier", 0), TIERS[0])
    rows = [[esc(e["ts"][:19]), esc(e["detail"])] for e in data.events(kind="risk", limit=100)]
    return (f"<p>Palier actuel : {badge(kind, text)} · drawdown {pct(info.get('drawdown', 0))} · perte du jour {pct(info.get('daily_loss', 0))}.</p>"
            "<p class=\"small muted\">Paliers : 5 % budgets réduits de moitié, 10 % plus aucun achat, 15 % arrêt global. Les stops de protection restent en place à l'arrêt.</p>"
            "<h2>Règles du RiskAgent déclenchées</h2>" + table(["Heure (UTC)", "Refus"], rows))


def experiment(data, csrf: str) -> str:
    e = data.experiment()
    rows = []
    for name, m in e["metrics"].items():
        rows.append([esc(name), str(m["days"]), pct(m["return"]), pct(m["drawdown"]), f'{m["sharpe"]:.2f}', f'{m["sortino"]:.2f}' if m["sortino"] != float("inf") else "∞"])
    team = e["metrics"].get("TEAM")
    ref = next((m for n, m in e["metrics"].items() if n.startswith("REF_")), None)
    checks = ""
    if team:
        ok1 = team["drawdown"] <= 0.15
        ok2 = ref is not None and team["drawdown"] < ref["drawdown"]
        checks = ("<h2>Critères de succès (EX-XP-03, à confirmer)</h2><ul>"
                  f"<li>Drawdown maximal ≤ 15 % : {badge('ok' if ok1 else 'crit', 'OUI' if ok1 else 'NON')}</li>"
                  f"<li>Drawdown inférieur à celui de la référence : {badge('ok' if ok2 else 'crit', 'OUI' if ok2 else 'NON')}</li></ul>")
    return (line_chart({"TEAM": e["team"], **e["refs"]})
            + "<h2>Indicateurs (valeurs quotidiennes)</h2>"
            + table(["Série", "Jours", "Rendement", "Drawdown max", "Sharpe", "Sortino"], rows, {1, 2, 3, 4, 5})
            + checks + '<p class="small muted">Une période de 90 jours est statistiquement courte : Sharpe et Sortino y sont très incertains, les conclusions sont indicatives (EX-XP-05).</p>')


def proposals(data, csrf: str) -> str:
    rows = []
    for p in data.proposals():
        if p["status"] == "pending":
            form = "".join(
                f'<form class="inline" method="post" action="/proposal"><input type="hidden" name="csrf" value="{esc(csrf)}">'
                f'<input type="hidden" name="id" value="{p["id"]}"><input type="hidden" name="decision" value="{d}"><button>{label}</button></form> '
                for d, label in (("accept", "Accepter"), ("reject", "Refuser"))
            )
        else:
            form = badge("ok" if p["status"] == "accepted" else "muted", p["status"])
        rows.append([str(p["id"]), esc(p["ts"][:16]), esc(p["kind"]), f'<b>{esc(p["title"])}</b><br><span class="small muted">{esc(p["payload_json"])}</span>', form])
    return ('<p class="small muted">Les propositions de l\'assistant ne changent jamais rien toutes seules. Accepter une proposition l\'enregistre : '
            "c'est à toi de l'appliquer dans la configuration (toute augmentation de risque exige ta validation).</p>"
            + table(["#", "Date", "Type", "Proposition", "Décision"], rows))


def assistant(data, csrf: str) -> str:
    rows = [[str(q["id"]), esc(q["ts"][:16]), esc(q["question"]), badge({"done": "ok", "pending": "muted", "error": "crit"}[q["status"]], q["status"]),
             f'<pre>{esc(q["answer"] or q["error"] or "")}</pre>'] for q in data.questions()]
    form = (f'<form method="post" action="/ask"><input type="hidden" name="csrf" value="{esc(csrf)}">'
            '<label for="q">Pose une question sur l\'équipe (réponse en quelques secondes si le worker de l\'assistant tourne)</label>'
            '<textarea id="q" name="question" rows="3" maxlength="500" required></textarea><p><button class="primary">Envoyer</button></p></form>')
    return (form + "<p class=\"small muted\">L'assistant est en lecture seule : il explique, résume et propose, il ne passe jamais d'ordre. "
            "Il tourne dans un processus séparé : ce tableau de bord n'a aucun accès à sa clé.</p>"
            + table(["#", "Date", "Question", "Statut", "Réponse"], rows))


def journal(data, csrf: str, level: str = "") -> str:
    levels = ["", "INFO", "WARNING", "ERROR", "CRITICAL", "REPORT"]
    links = " ".join(f'<a href="/journal?level={l}">{esc(l or "tous")}</a>' for l in levels)
    rows = [[esc(e["ts"][:19]), esc(e["level"]), esc(e["kind"]), esc(e["detail"])] for e in data.events(level=level or None, limit=200)]
    return f'<p class="small">Filtrer : {links}</p>' + table(["Heure (UTC)", "Niveau", "Type", "Détail"], rows)


HELP = [
    ("Drawdown", "Baisse de la valeur de l'équipe depuis son plus haut. 5 % : budgets réduits de moitié ; 10 % : plus aucun achat ; 15 % : arrêt global."),
    ("Régime de marché", "Classement de BTC : TENDANCE_HAUSSE, TENDANCE_BAISSE, RANGE (sans direction) ou HAUTE_VOLATILITE. Il module le budget de chaque stratège."),
    ("Sortino", "Rendement rapporté aux seules baisses (plus c'est haut, mieux c'est). Sharpe : rapporté à toute la variabilité. Sur une période courte, ils sont très incertains."),
    ("Kill switch", "Arrêt d'urgence : annule les ordres ouverts et arrête les agents. Les stops de protection posés chez Binance restent en place."),
    ("Stop de protection", "Ordre posé chez Binance qui ferme la position si le prix tombe trop bas, même si le programme est arrêté."),
    ("Paper", "Simulation : prix réels, ordres et soldes simulés localement. Aucun ordre n'atteint Binance."),
    ("Testnet", "Environnement d'essai de Binance avec de l'argent virtuel ; ses prix ne suivent pas le marché réel."),
    ("Palier", "Étape de protection automatique selon le drawdown (voir Risque)."),
    ("Budget / allocation", "Part du capital plafonné confiée à chaque stratège. Le multiplicateur de régime peut la réduire, jamais l'augmenter."),
    ("Proposition", "Suggestion de l'assistant, déposée en attente. Rien ne change sans ta décision."),
]


def help_page(data, csrf: str) -> str:
    items = "".join(f"<div class=\"card\"><b>{esc(t)}</b><div class=\"small\">{esc(d)}</div></div>" for t, d in HELP)
    return f'<div class="grid">{items}</div><p class="small muted">Les cartes détaillées de chaque agent sont dans <code>docs/agents/</code>. Ceci n\'est pas un conseil financier.</p>'
