"""EX-SEC-06 : aucun appel de retrait ou de conversion dans le code source."""

import re
from pathlib import Path

SRC = Path(__file__).parents[2] / "src"
FORBIDDEN = re.compile(
    r"\bwithdraw\w*\s*\(|capital/withdraw|convert_?trade|sapiPost\w*(withdraw|transfer)|\btransfer\w*\s*\(",
    re.IGNORECASE,
)


def _has_forbidden_call(path) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        if "# audit-pattern" in line:      # motif de détection de l'outil d'audit : explicitement marqué, jamais un appel
            continue
        if FORBIDDEN.search(line):
            return True
    return False


def test_no_withdraw_or_convert_calls_in_source():
    offenders = [str(p) for p in SRC.rglob("*.py") if _has_forbidden_call(p)]
    assert offenders == []
