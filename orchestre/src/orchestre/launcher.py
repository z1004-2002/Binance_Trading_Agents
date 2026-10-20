"""Lanceur global : démarre et supervise tout le système (moteur, tableau de bord, assistant) d'une seule commande.

- chaque composant tourne dans son propre processus, avec son journal (var/logs/) ;
- un composant qui plante est relancé avec un délai croissant (plafonné) ; une sortie normale n'est pas relancée ;
- l'arrêt est progressif : SIGINT (arrêt propre), puis SIGTERM, puis SIGKILL ;
- le tableau de bord et l'assistant ne reçoivent QUE leur propre environnement (EX-UI-08) : jamais les clés d'exchange ;
- le mode réel n'est jamais lancé par ce script : il passe par `run --live` et la porte du §20.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from orchestre.config import ConfigError, load_settings
from orchestre.runtime.builder import strategy_symbols

START_WAIT = 90.0       # attente maximale de la base de données avant de démarrer un composant qui en dépend
KEEP_ENV = ("PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "TZ")


class LauncherError(RuntimeError):
    pass


@dataclass
class Component:
    name: str
    argv: list[str]
    env: dict[str, str] | None = None      # None : environnement hérité ; sinon : environnement explicite et minimal
    restart: bool = True
    url: str = ""
    wait_for: Path | None = None           # fichier à attendre avant de démarrer (la base créée par le moteur)


@dataclass
class _State:
    component: Component
    log: Path
    proc: object | None = None
    status: str = "en attente"
    started_at: str | None = None
    restarts_count: int = 0
    restart_times: list[float] = field(default_factory=list)
    next_restart: float | None = None
    waiting_since: float | None = None


# ---------- processus ----------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"      # un zombie n'est plus vivant
    except (OSError, IndexError):
        return True


def running_pid(run_dir: Path) -> int | None:
    try:
        pid = int((Path(run_dir) / "up.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def stop_running(run_dir: Path, timeout: float = 30.0) -> bool:
    """Demande l'arrêt du lanceur en cours (SIGTERM) et attend sa disparition. False s'il n'y en avait pas."""
    pid = running_pid(run_dir)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _alive(pid)


def minimal_env() -> dict[str, str]:
    return {k: os.environ[k] for k in KEEP_ENV if k in os.environ}


# ---------- supervision ----------

class Launcher:
    def __init__(
        self, components: list[Component], log_dir: Path, run_dir: Path,
        spawn: Callable = subprocess.Popen, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep, max_restarts: int = 5, window: float = 600.0,
        term_timeout: float = 15.0, out=None,
    ) -> None:
        self.log_dir, self.run_dir = Path(log_dir), Path(run_dir)
        self.states = [_State(c, self.log_dir / f"{self._slug(c.name)}.log") for c in components]
        self._spawn, self._clock, self._sleep = spawn, clock, sleep
        self._max_restarts, self._window, self._term_timeout, self._out = max_restarts, window, term_timeout, out
        self._stop_requested = False

    @staticmethod
    def _slug(name: str) -> str:
        return name.replace(" ", "-").replace("é", "e").replace("è", "e")

    def _say(self, text: str) -> None:
        if self._out is not None:
            print(text, file=self._out, flush=True)

    # --- démarrage ---
    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        existing = running_pid(self.run_dir)
        if existing is not None:
            raise LauncherError(f"le système est déjà démarré (pid {existing}) : `down` pour l'arrêter")
        (self.run_dir / "up.pid").write_text(str(os.getpid()))
        for state in self.states:
            if state.component.wait_for is not None and not state.component.wait_for.exists():
                state.status, state.waiting_since = "en attente", self._clock()
            else:
                self._launch(state)
        self._write_status()

    def _launch(self, state: _State) -> None:
        c = state.component
        env = dict(os.environ) if c.env is None else dict(c.env)
        with open(state.log, "ab") as log:
            log.write(f"\n--- démarrage {datetime.now(timezone.utc).isoformat()} : {' '.join(c.argv[:6])} ...\n".encode())
            state.proc = self._spawn(c.argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                     start_new_session=True)
        state.status, state.started_at, state.next_restart = "en marche", datetime.now(timezone.utc).isoformat(), None

    # --- surveillance ---
    def poll(self) -> None:
        now = self._clock()
        for state in self.states:
            c = state.component
            if state.status == "en attente":
                if c.wait_for is not None and c.wait_for.exists():
                    self._launch(state)
                elif state.waiting_since is not None and now - state.waiting_since > START_WAIT:
                    state.status = "en échec"
                    self._say(f"[{c.name}] la base {c.wait_for} n'est pas apparue : composant non démarré")
            elif state.status == "redémarrage prévu":
                if state.next_restart is not None and now >= state.next_restart:
                    self._launch(state)
                    state.restarts_count += 1
                    self._say(f"[{c.name}] relancé ({state.restarts_count})")
            elif state.status == "en marche":
                code = state.proc.poll()
                if code is None:
                    continue
                if code == 0:
                    state.status = "terminé"
                    self._say(f"[{c.name}] terminé normalement")
                    continue
                recent = [t for t in state.restart_times if now - t < self._window]
                if not c.restart or len(recent) >= self._max_restarts:
                    state.status = "en échec"
                    self._say(f"[{c.name}] ÉCHEC (code {code}) : voir {state.log}")
                    continue
                state.restart_times.append(now)
                state.next_restart = now + min(60.0, 2.0 ** len(recent))
                state.status = "redémarrage prévu"
                self._say(f"[{c.name}] a planté (code {code}) : relance dans {state.next_restart - now:.0f} s")
        self._write_status()

    def _write_status(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        data = {"supervisor_pid": os.getpid(), "components": [
            {"name": s.component.name, "pid": getattr(s.proc, "pid", None), "status": s.status, "restarts": s.restarts_count,
             "started_at": s.started_at, "url": s.component.url, "log": str(s.log)} for s in self.states]}
        (self.run_dir / "up.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))

    # --- boucle et arrêt ---
    def request_stop(self, *_args) -> None:
        self._stop_requested = True

    def run_forever(self, interval: float = 1.0, max_loops: int | None = None) -> None:
        try:
            signal.signal(signal.SIGINT, self.request_stop)
            signal.signal(signal.SIGTERM, self.request_stop)
        except ValueError:      # hors du fil principal (tests)
            pass
        loops = 0
        while not self._stop_requested and (max_loops is None or loops < max_loops):
            self.poll()
            loops += 1
            self._sleep(interval)
        self.stop()

    def _wait(self, procs: list, timeout: float) -> None:
        deadline = self._clock() + timeout
        while any(p.poll() is None for p in procs) and self._clock() < deadline:
            self._sleep(0.1)

    def stop(self, term_timeout: float | None = None) -> None:
        timeout = self._term_timeout if term_timeout is None else term_timeout
        live = [s for s in self.states if s.proc is not None and s.proc.poll() is None]
        for s in live:
            s.proc.send_signal(signal.SIGINT)                   # arrêt propre : le moteur termine son tick et sauvegarde
        self._wait([s.proc for s in live], timeout)
        for s in live:
            if s.proc.poll() is None:
                self._say(f"[{s.component.name}] ne répond pas à l'arrêt propre : SIGTERM")
                s.proc.terminate()
        self._wait([s.proc for s in live], 5.0)
        for s in live:
            if s.proc.poll() is None:
                self._say(f"[{s.component.name}] bloqué : SIGKILL")
                s.proc.kill()
        for s in self.states:
            if s.status in ("en marche", "redémarrage prévu", "en attente"):
                s.status = "arrêté"
        self._write_status()
        pid_file = self.run_dir / "up.pid"
        if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()


# ---------- plan : quels composants lancer ----------

@dataclass
class Plan:
    components: list[Component]
    warnings: list[str]
    db: str
    mode: str


def read_env_names(path: str | Path) -> dict[str, int]:
    """Noms des variables NON VIDES d'un fichier .env avec la longueur de leur valeur : jamais les valeurs."""
    names: dict[str, int] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return names
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if value:
            names[key.strip()] = len(value)
    return names


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # comme le serveur : un port en TIME_WAIT après un arrêt récent est libre
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def plan_components(args, present: Mapping[str, int], port_free: Callable[[int], bool] = port_is_free) -> Plan:
    if args.mode not in ("paper", "testnet"):
        raise ConfigError("mode inconnu : `up` ne lance que paper ou testnet (le mode réel passe par `run --live` et la porte du §20)")
    try:
        raw = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"configuration illisible ({args.config}) : {exc}") from exc
    if raw.get("env") == "live":
        raise ConfigError("la configuration est en mode réel : `up` ne lance jamais l'argent réel ; utiliser `run --live` après la porte du §20")
    load_settings(args.config)
    if not strategy_symbols(raw):
        raise ConfigError("aucune stratégie activée (strategies.<nom>.enabled = true)")

    paper = args.mode == "paper"
    if not paper:
        missing = [k for k in ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET") if k not in present]
        if missing:
            raise ConfigError(f"clés testnet absentes de {args.env_file} : {', '.join(missing)}")
    db = args.db or f"var/{'paper' if paper else 'testnet'}.db"
    py, warnings = args.python, []
    cli = [py, "-m", "orchestre.cli"]

    engine = [*cli, "run", "--config", args.config, "--db", db, "--stop-file", args.stop_file,
              "--interval", str(args.interval), "--env-file", args.env_file]
    if paper:
        engine.append("--paper")
        if args.paper_cash is not None:
            engine += ["--paper-cash", str(args.paper_cash)]
    if args.no_analysis:
        engine.append("--no-analysis")
    if args.ticks is not None:
        engine += ["--ticks", str(args.ticks)]
    components = [Component("moteur", engine)]
    twin_db = None
    if getattr(args, "twin", False):
        # jumeau numérique : même configuration, en paper, sur sa propre base et son propre fichier d'arrêt
        twin_db = str(Path(db).with_name(Path(db).stem + "-twin.db"))
        twin = [*cli, "run", "--config", args.config, "--db", twin_db, "--stop-file", args.stop_file + "-twin",
                "--interval", str(args.interval), "--env-file", args.env_file, "--paper"]
        if args.paper_cash is not None:
            twin += ["--paper-cash", str(args.paper_cash)]
        if args.no_analysis:
            twin.append("--no-analysis")
        if args.ticks is not None:
            twin += ["--ticks", str(args.ticks)]
        components.append(Component("jumeau", twin))
        if paper:
            warnings.append("le jumeau d'un système déjà en paper est une copie presque identique : il est surtout utile avec --mode testnet")

    if not args.no_dashboard:
        if present.get("DASHBOARD_PASSWORD", 0) < 8:
            warnings.append("tableau de bord non démarré : DASHBOARD_PASSWORD absent ou de moins de 8 caractères dans .env")
        elif not port_free(args.port):
            warnings.append(f"tableau de bord non démarré : le port {args.port} est occupé (essayer --port)")
        else:
            dashboard = [*cli, "dashboard", "--db", db, "--stop-file", args.stop_file, "--port", str(args.port), "--env-file", args.env_file]
            if twin_db:
                dashboard += ["--twin-db", twin_db]
            components.append(Component(
                "tableau de bord", dashboard,
                env=minimal_env(), url=f"http://127.0.0.1:{args.port}", wait_for=Path(db)))
    if not args.no_assistant:
        if "GROQ_API_KEY" not in present or "GROQ_MODEL" not in present:
            warnings.append("assistant non démarré : GROQ_API_KEY et GROQ_MODEL absents de .env (optionnel)")
        else:
            components.append(Component(
                "assistant", [*cli, "assistant", "worker", "--db", db, "--env-file", args.env_file],
                env=minimal_env(), wait_for=Path(db)))
    return Plan(components, warnings, db, args.mode)


# ---------- commandes de la CLI : up / down / ps ----------

def _banner(plan: Plan, launcher: Launcher, out) -> None:
    label = {"paper": "PAPER (simulation : marché réel, ordres simulés)", "testnet": "TESTNET (argent virtuel)"}[plan.mode]
    print(f"ORCHESTRE démarré en mode {label}", file=out)
    for state in launcher.states:
        c = state.component
        pid = getattr(state.proc, "pid", None)
        extra = f"  {c.url}" if c.url else ""
        print(f"  {c.name:<16} {state.status:<10} pid {pid or '-':<7} journal {state.log}{extra}", file=out)
    for w in plan.warnings:
        print(f"  ! {w}", file=out)
    print("Arrêt : Ctrl+C ici, ou `./orchestre.sh down` depuis un autre terminal. État : `./orchestre.sh ps`.", file=out)


def cmd_up(args, out, err, spawn: Callable = subprocess.Popen) -> int:
    args.python = sys.executable
    present = read_env_names(args.env_file)
    plan = plan_components(args, present)
    if args.check:
        print(f"Plan ({plan.mode}) : " + ", ".join(c.name for c in plan.components), file=out)
        for w in plan.warnings:
            print(f"  ! {w}", file=out)
        return 0
    base = Path(plan.db).parent
    if args.detach:
        base.joinpath("logs").mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, "-m", "orchestre.cli", "up", "--mode", args.mode, "--config", args.config, "--db", plan.db,
                "--stop-file", args.stop_file, "--port", str(args.port), "--interval", str(args.interval),
                "--env-file", args.env_file]
        argv += [f for f, on in (("--no-dashboard", args.no_dashboard), ("--no-assistant", args.no_assistant),
                                 ("--no-analysis", args.no_analysis), ("--twin", getattr(args, "twin", False))) if on]
        if args.paper_cash is not None:
            argv += ["--paper-cash", str(args.paper_cash)]
        with open(base / "logs" / "up.log", "ab") as log:
            proc = spawn(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        print(f"Système lancé en arrière-plan (pid {proc.pid}). Journaux : {base / 'logs'}. Arrêt : `./orchestre.sh down`.", file=out)
        return 0
    launcher = Launcher(plan.components, base / "logs", base / "run", out=out)
    try:
        launcher.start()
    except LauncherError as exc:
        print(f"Erreur : {exc}", file=err)
        return 2
    _banner(plan, launcher, out)
    launcher.run_forever()
    print("Système arrêté.", file=out)
    return 0


def cmd_down(args, out, err) -> int:
    run_dir = Path(args.run_dir)
    if stop_running(run_dir, timeout=args.timeout):
        print("Système arrêté.", file=out)
        return 0
    if running_pid(run_dir) is None:
        print("Aucun système en cours (rien à arrêter).", file=out)
        return 0
    print("Le système ne s'est pas arrêté à temps : voir `ps` et les journaux.", file=err)
    return 1


def cmd_ps(args, out, err) -> int:
    run_dir = Path(args.run_dir)
    pid = running_pid(run_dir)
    try:
        data = json.loads((run_dir / "up.json").read_text())
    except (OSError, ValueError):
        print("Aucun état enregistré : le système n'a pas été lancé par `up`.", file=out)
        return 0
    print(f"Lanceur : {'EN COURS (pid ' + str(pid) + ')' if pid else 'ARRÊTÉ'}", file=out)
    for c in data["components"]:
        alive = c["pid"] is not None and _alive(c["pid"])
        state = c["status"] if pid else ("actif (orphelin)" if alive else c["status"])
        print(f"  {c['name']:<16} {state:<18} pid {c['pid'] or '-':<7} relances {c['restarts']} {c['url']}", file=out)
    return 0
