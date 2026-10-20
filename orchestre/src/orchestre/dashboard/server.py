"""Serveur du tableau de bord (EX-UI-01, EX-UI-07, EX-UI-08). Bibliothèque standard uniquement.

- écoute sur 127.0.0.1 seulement ; hôtes acceptés : localhost ; aucune ressource externe (CSP stricte) ;
- connexion par mot de passe (PBKDF2), pause après 5 échecs ; cookie HttpOnly + SameSite=Strict ; jeton CSRF sur tout POST ;
- lecture seule de la base ; trois actions d'écriture seulement, via la table des commandes : pause/reprise/kill,
  décision sur une proposition, question à l'assistant. Aucune saisie d'ordre manuelle (EX-UI-03) ;
- aucune clé Binance ni Groq n'est lue par ce module (EX-UI-08).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit

from orchestre.dashboard import views
from orchestre.dashboard.assets import CSS, JS
from orchestre.dashboard.data import DashboardData
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SESSION_TTL = 8 * 3600
MAX_BODY = 10_000
MAX_ATTEMPTS, LOCK_SECONDS = 5, 60
COMMANDS = {"pause", "resume", "kill", "resume_pair"}
CSP = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"

PAGES: dict[str, Callable] = {
    "/": views.overview, "/agents": views.agents, "/allocation": views.allocation, "/market": views.market,
    "/orders": views.orders, "/risk": views.risk, "/experiment": views.experiment, "/proposals": views.proposals,
    "/assistant": views.assistant, "/help": views.help_page,
}
TITLES = {
    "/": "Vue d'ensemble", "/agents": "Agents", "/allocation": "Allocation", "/market": "Marché", "/orders": "Ordres et exécutions",
    "/risk": "Risque", "/experiment": "Expérience", "/proposals": "Propositions", "/assistant": "Assistant",
    "/journal": "Journal", "/help": "Aide",
}


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


def _hash(password: str, salt: bytes, iterations: int = 200_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


class CommandWriter:
    """Seul chemin d'écriture du tableau de bord : trois actions, rien d'autre (pas d'ordre, pas de configuration)."""

    def __init__(self, db_path, clock: Callable[[], datetime]) -> None:
        self._store, self._clock = Store(Database(db_path)), clock

    def command(self, command: str, target: str) -> None:
        self._store.enqueue_command(self._clock(), command, target or None)

    def decide(self, proposal_id: int, status: str) -> bool:
        return self._store.decide_proposal(proposal_id, status, self._clock())

    def ask(self, question: str) -> None:
        self._store.enqueue_question(self._clock(), question)


class DashboardServer:
    def __init__(
        self, db_path, stop_file, password: str, host: str = "127.0.0.1", port: int = 8765,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), kdf_iterations: int = 200_000,
    ) -> None:
        if host not in LOOPBACK:
            raise ValueError("le tableau de bord n'écoute que sur 127.0.0.1 (jamais exposé publiquement, EX-UI-01)")
        if len(password) < 8:
            raise ValueError("mot de passe trop court : 8 caractères minimum (variable DASHBOARD_PASSWORD)")
        self.data = DashboardData(db_path, stop_file, clock)
        self.writer = CommandWriter(db_path, clock)
        self._clock = clock
        self._salt = secrets.token_bytes(16)
        self._iterations = kdf_iterations
        self._password_hash = _hash(password, self._salt, kdf_iterations)
        self.sessions: dict[str, dict] = {}
        self.failures: dict[str, tuple[int, float]] = {}
        self.httpd = ThreadingHTTPServer((host, port), self._handler())
        self.port = self.httpd.server_address[1]

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.05)

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ORCHESTRE"

            def log_message(self, *args):  # pas de journal d'accès sur la sortie standard
                pass

            def _serve(self, method: str) -> None:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                body = self.rfile.read(length) if length else b""
                r = app.handle(method, self.path, dict(self.headers), body, self.client_address[0])
                self.send_response(r.status)
                for key, value in {**app.security_headers(), **r.headers}.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(r.body)))
                self.end_headers()
                self.wfile.write(r.body)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        return Handler

    # --- réponses ---
    @staticmethod
    def security_headers() -> dict:
        return {
            "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
        }

    @staticmethod
    def page(status: int, html: str, headers: dict | None = None) -> Response:
        return Response(status, html.encode("utf-8"), {"Content-Type": "text/html; charset=utf-8", **(headers or {})})

    @staticmethod
    def redirect(location: str, headers: dict | None = None) -> Response:
        return Response(303, b"", {"Location": location, **(headers or {})})

    @staticmethod
    def text(status: int, message: str) -> Response:
        return Response(status, message.encode("utf-8"), {"Content-Type": "text/plain; charset=utf-8"})

    # --- sessions ---
    def _session(self, headers: dict) -> dict | None:
        cookie = SimpleCookie(headers.get("Cookie", ""))
        sid = cookie["sid"].value if "sid" in cookie else None
        session = self.sessions.get(sid) if sid else None
        if session and session["expires"] > time.time():
            return session
        self.sessions.pop(sid, None)
        return None

    def _login(self, ip: str, form: dict) -> Response:
        count, until = self.failures.get(ip, (0, 0.0))
        if until > time.time():
            return self.page(429, views.login_page("Trop d'essais : réessaie dans une minute."))
        candidate = _hash((form.get("password") or [""])[0], self._salt, self._iterations)
        if not hmac.compare_digest(candidate, self._password_hash):
            count += 1
            self.failures[ip] = (count, time.time() + LOCK_SECONDS if count >= MAX_ATTEMPTS else 0.0)
            return self.page(401, views.login_page("Mot de passe incorrect."))
        self.failures.pop(ip, None)
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {"csrf": secrets.token_urlsafe(24), "expires": time.time() + SESSION_TTL}
        return self.redirect("/", {"Set-Cookie": f"sid={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"})

    # --- routage ---
    def handle(self, method: str, raw_path: str, headers: dict, body: bytes, ip: str) -> Response:
        if headers.get("Host", "").rsplit(":", 1)[0] not in ALLOWED_HOSTS:
            return self.text(400, "Hôte non autorisé")
        url = urlsplit(raw_path)
        path, query = url.path, parse_qs(url.query)
        if path.startswith("/static/"):
            return self._static(path)
        form = parse_qs(body.decode("utf-8", "replace")) if method == "POST" else {}
        if path == "/login":
            return self._login(ip, form) if method == "POST" else self.page(200, views.login_page())
        session = self._session(headers)
        if session is None:
            return self.redirect("/login")
        if method == "POST":
            if not hmac.compare_digest((form.get("csrf") or [""])[0], session["csrf"]):
                return self.text(403, "Jeton CSRF invalide")
            return self._post(path, form, headers, session)
        if path == "/api/status":
            info = self.data.info()
            return Response(200, json.dumps({
                "env": info.get("env"), "mode": info.get("mode"), "tier": info.get("tier", 0), "kill": info.get("kill", False),
                "tick_age": info.get("tick_age"), "drawdown": info.get("drawdown", 0.0),
            }).encode(), {"Content-Type": "application/json"})
        msg = (query.get("msg") or [""])[0]
        if path == "/journal":
            level = (query.get("level") or [""])[0]
            content = views.journal(self.data, session["csrf"], level if level in {"INFO", "WARNING", "ERROR", "CRITICAL", "REPORT"} else "")
        elif path in PAGES:
            content = PAGES[path](self.data, session["csrf"])
        else:
            return self.text(404, "Page introuvable")
        info = self.data.info()
        return self.page(200, views.layout(TITLES[path], content, path, info, session["csrf"], msg, autorefresh=path in ("/", "/agents")))

    def _static(self, path: str) -> Response:
        assets = {"/static/app.css": (CSS, "text/css; charset=utf-8"), "/static/app.js": (JS, "text/javascript; charset=utf-8")}
        if path not in assets:
            return self.text(404, "Introuvable")
        content, kind = assets[path]
        return Response(200, content.encode("utf-8"), {"Content-Type": kind})

    def _post(self, path: str, form: dict, headers: dict, session: dict) -> Response:
        first = lambda name: (form.get(name) or [""])[0]  # noqa: E731
        back = lambda page, msg: self.redirect(f"{page}?msg={quote(msg)}")  # noqa: E731
        if path == "/logout":
            cookie = SimpleCookie(headers.get("Cookie", ""))
            self.sessions.pop(cookie["sid"].value, None)
            return self.redirect("/login", {"Set-Cookie": "sid=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})
        if path == "/command":
            command, target = first("command"), first("target")
            if command not in COMMANDS:
                return back("/agents", "Commande refusée : seules pause, reprise et arrêt d'urgence existent.")
            if command == "kill":
                if first("confirm") != "STOP":
                    return back("/agents", "Arrêt d'urgence NON activé : il faut taper STOP en majuscules.")
                target = ""
            elif command == "resume_pair":
                if target not in (self.data.info().get("paused_pairs") or {}):
                    return back("/market", "Commande refusée : cette paire n'est pas en pause.")
                self.writer.command(command, target)
                return back("/market", f"Reprise de {target} envoyée au superviseur.")
            elif target != "all" and target not in self.data.agent_ids():
                return back("/agents", "Commande refusée : agent inconnu.")
            self.writer.command(command, target)
            return back("/agents", f"Commande « {command} » envoyée au superviseur (appliquée au prochain tick).")
        if path == "/proposal":
            decision = {"accept": "accepted", "reject": "rejected"}.get(first("decision"))
            if decision is None or not first("id").isdigit():
                return back("/proposals", "Décision invalide.")
            done = self.writer.decide(int(first("id")), decision)
            return back("/proposals", "Décision enregistrée." if done else "Cette proposition a déjà été décidée.")
        if path == "/ask":
            question = first("question").strip()
            if not question or len(question) > 500:
                return back("/assistant", "Question vide ou trop longue (500 caractères maximum).")
            self.writer.ask(question)
            return back("/assistant", "Question envoyée à l'assistant.")
        return self.text(404, "Page introuvable")
