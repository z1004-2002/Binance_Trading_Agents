"""EX-SEC-06 : aucun appel de retrait ou de conversion dans le code source."""

import re
from pathlib import Path

SRC = Path(__file__).parents[2] / "src"
FORBIDDEN = re.compile(r"withdraw|convert_?trade|sapi/v1/capital/withdraw", re.IGNORECASE)


def test_no_withdraw_or_convert_calls_in_source():
    offenders = [
        str(p)
        for p in SRC.rglob("*.py")
        if FORBIDDEN.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
