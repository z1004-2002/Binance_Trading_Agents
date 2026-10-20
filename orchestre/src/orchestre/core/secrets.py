"""Filtre de masquage des secrets avant journalisation (EX-SEC-08)."""

import re

_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|signature|token|password)\b(\s*[=:]\s*)([^\s,;&\"']+)"
)


def mask_secrets(text: str) -> str:
    return _PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
