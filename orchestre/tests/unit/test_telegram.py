import json

from orchestre.notify.telegram import TelegramNotifier, from_env

TOKEN = "123456789:AAExampleTokenForTestsOnly_abcdefghijk"


class Spy:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, payload):
        if self.fail:
            raise OSError("réseau")
        self.calls.append((url, payload))


def make(spy=None, **kw):
    clock = {"t": 0.0}
    spy = spy or Spy()
    n = TelegramNotifier(TOKEN, "42", post=spy, clock=lambda: clock["t"], **kw)
    return n, spy, clock


def test_only_warning_and_above_and_reports_are_sent():
    n, spy, _ = make()
    for level in ("INFO", "WARNING", "ERROR", "CRITICAL", "REPORT"):
        n(level, f"texte {level}")
    assert [c[1]["text"].split("]")[0] for c in spy.calls] == ["[WARNING", "[ERROR", "[CRITICAL", "[REPORT"]


def test_message_goes_to_the_bot_api_with_the_chat_id():
    n, spy, _ = make()
    n("CRITICAL", "kill switch activé")
    url, payload = spy.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage" and payload["chat_id"] == "42"
    assert "kill switch activé" in payload["text"]


def test_identical_alerts_are_deduplicated_within_the_window_but_not_reports():
    n, spy, clock = make(dedup_seconds=300)
    n("CRITICAL", "agent grid silencieux")
    n("CRITICAL", "agent grid silencieux")
    assert len(spy.calls) == 1
    clock["t"] = 301
    n("CRITICAL", "agent grid silencieux")
    assert len(spy.calls) == 2
    n("REPORT", "rapport")
    n("REPORT", "rapport")
    assert len(spy.calls) == 4


def test_secrets_and_the_bot_token_never_leave_in_a_message():
    n, spy, _ = make()
    n("ERROR", f"échec avec apiKey=SECRETVALUE1234567890 et le jeton {TOKEN}")
    text = spy.calls[0][1]["text"]
    assert "SECRETVALUE1234567890" not in text and TOKEN not in text


def test_long_messages_are_truncated():
    n, spy, _ = make()
    n("ERROR", "x" * 10_000)
    assert len(spy.calls[0][1]["text"]) <= 4000


def test_a_network_failure_never_raises_and_is_counted():
    n, _, _ = make(Spy(fail=True))
    n("CRITICAL", "x")
    assert n.failures == 1


def test_from_env_needs_both_variables():
    assert from_env({}) is None and from_env({"TELEGRAM_BOT_TOKEN": TOKEN}) is None
    assert from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "42"}, post=Spy()) is not None
