"""Worker de l'assistant : répond aux questions du tableau de bord et envoie le rapport quotidien (EX-NOT-03).

Processus séparé, seul à détenir la clé Groq (EX-UI-08). Une panne ne touche jamais l'équipe de trading (EX-LLM-02).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from orchestre.assistant.agent import AssistantAgent, BudgetExceeded
from orchestre.assistant.groq import GroqError
from orchestre.persistence.store import Store


def process_pending(store: Store, assistant: AssistantAgent, clock: Callable[[], datetime], limit: int = 5) -> int:
    done = 0
    while done < limit and (question := store.next_question()) is not None:
        try:
            store.answer_question(question["id"], assistant.ask(question["question"]), None, clock())
        except (BudgetExceeded, GroqError) as exc:
            store.answer_question(question["id"], None, str(exc), clock())
        except Exception:
            store.answer_question(question["id"], None, "erreur interne de l'assistant", clock())
        done += 1
    return done


def maybe_daily_report(
    store: Store, assistant: AssistantAgent, clock: Callable[[], datetime],
    notifier: Callable[[str, str], None] | None, hour: int = 7,
) -> bool:
    now = clock()
    last = (store.load_state("assistant_worker") or {}).get("last_report_day")
    if now.hour < hour or last == now.date().isoformat():
        return False
    report = assistant.daily_report()
    store.save_state("assistant_worker", {"last_report_day": now.date().isoformat()}, now)
    store.log_event(now, "REPORT", "assistant_report", report[:1000])
    if notifier is not None:
        notifier("REPORT", report)
    return True


def run_worker(
    store: Store, assistant: AssistantAgent, clock: Callable[[], datetime], notifier=None, interval: float = 5.0,
    sleep: Callable[[float], None] = time.sleep, max_loops: int | None = None, report_hour: int = 7,
) -> None:
    loops = 0
    while max_loops is None or loops < max_loops:
        try:
            process_pending(store, assistant, clock)
            maybe_daily_report(store, assistant, clock, notifier, report_hour)
        except Exception as exc:  # le worker ne doit jamais s'arrêter sur une panne passagère
            store.log_event(clock(), "ERROR", "assistant", f"worker : {exc!r}")
        loops += 1
        sleep(interval)
