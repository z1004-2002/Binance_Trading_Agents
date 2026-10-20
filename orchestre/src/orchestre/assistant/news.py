"""Actualités (EX-AN-14, EX-LLM-05, EX-LLM-07) : flux RSS, classification en sortie structurée VALIDÉE par notre code.

Les titres sont des DONNÉES hostiles potentielles (injection de prompt [38]). Le modèle de classification n'a aucun outil ;
tout ce qui sort du schéma est écarté (jamais corrigé) ; l'effet maximal d'une classification est de mettre une paire en
pause, c'est-à-dire de réduire le risque (EX-AN-15).
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

CATEGORIES = {"régulation", "piratage", "macroéconomie", "cotation", "autre"}
PAUSE_CATEGORIES = {"piratage", "régulation"}
MAX_XML = 1_000_000
MAX_TITLE = 200
MAX_TITLES = 30
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NewsItem:
    title: str
    asset: str | None
    tone: float
    category: str
    severity: int


def default_fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "orchestre/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 : URL fournie par le porteur
        return response.read(MAX_XML + 1).decode("utf-8", "replace")


def _clean(title: str) -> str:
    title = _TAGS.sub("", _CONTROL.sub("", title))
    return re.sub(r"\s+", " ", title).strip()[:MAX_TITLE]


def parse_rss(xml_text: str, limit: int = MAX_TITLES) -> list[str]:
    """Titres d'un flux RSS ou Atom. Document trop gros ou avec DTD/entités : refusé (bombes XML)."""
    if len(xml_text) > MAX_XML or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    titles = []
    for node in root.iter():
        if node.tag.split("}")[-1] in ("item", "entry"):
            for child in node:
                if child.tag.split("}")[-1] == "title" and child.text:
                    cleaned = _clean(child.text)
                    if cleaned:
                        titles.append(cleaned)
    return titles[:limit]


def parse_classification(content: str, titles: list[str], assets: set[str]) -> list[NewsItem]:
    try:
        raw = json.loads(content)["items"]
    except (ValueError, KeyError, TypeError):
        return []
    items: dict[int, NewsItem] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        index, asset, tone, category, severity = (entry.get(k) for k in ("index", "asset", "tone", "category", "severity"))
        valid = (
            type(index) is int and 0 <= index < len(titles) and index not in items
            and (asset is None or asset in assets)
            and isinstance(tone, (int, float)) and not isinstance(tone, bool) and -1 <= tone <= 1
            and category in CATEGORIES and type(severity) is int and 1 <= severity <= 5
        )
        if valid:
            items[index] = NewsItem(titles[index], asset, float(tone), category, severity)
    return [items[i] for i in sorted(items)]


def pairs_to_pause(items: list[NewsItem], quote: str = "USDT") -> dict[str, str]:
    pairs: dict[str, str] = {}
    for it in items:
        if it.asset and it.severity >= 4 and it.category in PAUSE_CATEGORIES and it.tone <= -0.5:
            pairs.setdefault(f"{it.asset}{quote}", f"{it.category} (gravité {it.severity}) : {it.title[:80]}")
    return pairs
