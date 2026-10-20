"""Client Groq (API compatible « chat completions »), bibliothèque standard uniquement (C-07, EX-LLM-04).

La clé ne figure jamais dans une erreur ni dans un journal (EX-SEC-08). Le modèle est choisi par le porteur dans sa
console Groq : aucun identifiant de modèle n'est figé ici.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from orchestre.core.secrets import mask_secrets

BASE_URL = "https://api.groq.com/openai/v1"


class GroqError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResult:
    message: dict
    tokens_in: int
    tokens_out: int


def default_post(url: str, headers: dict, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 : URL https constante
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise GroqError(f"HTTP {exc.code}") from None


class GroqClient:
    def __init__(self, api_key: str, model: str, post: Callable[[str, dict, dict], dict] = default_post, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise GroqError("GROQ_API_KEY manquante (à définir dans .env)")
        if not model:
            raise GroqError("GROQ_MODEL manquant : choisir un modèle dans la console Groq et le définir dans .env")
        self._key, self.model, self._post, self._url = api_key, model, post, f"{base_url}/chat/completions"

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
        temperature: float = 0.2, max_tokens: int = 900,
    ) -> ChatResult:
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            raw = self._post(self._url, {"Authorization": f"Bearer {self._key}"}, body)
        except GroqError:
            raise
        except Exception as exc:
            raise GroqError(mask_secrets(str(exc)).replace(self._key, "***")) from None
        try:
            message = raw["choices"][0]["message"]
            usage = raw.get("usage", {})
            return ChatResult(message, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GroqError(f"réponse Groq inattendue : {exc!r}") from None
