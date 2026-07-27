"""Azure OpenAI chat helper for the SM-R4 eval-data build ONLY.

This is used to author the *independent* synthetic memory set (generation +
blind relevance labeling) so the eval is not single-authored by whoever wrote
the harness. It is NOT used to score or rank the embedding models — those are
measured directly. Credentials come from the environment (never committed):

    AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY /
    AZURE_OPENAI_API_VERSION / AZURE_OPENAI_DEPLOYMENT

gpt-5.x is a reasoning model, so we use `max_completion_tokens` and do not set
`temperature`.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


def _client():
    from openai import AzureOpenAI

    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    )


class LLM:
    """Thin wrapper: a JSON-returning chat call with retries."""

    def __init__(
        self, deployment: Optional[str] = None, max_completion_tokens: int = 16000
    ):
        self.deployment = deployment or os.environ["AZURE_OPENAI_DEPLOYMENT"]
        self.max_completion_tokens = max_completion_tokens
        self._c = _client()

    def json(self, system: str, user: str, retries: int = 3) -> Any:
        """Return parsed JSON from the model, retrying on transient/parse errors."""
        last = None
        for attempt in range(retries):
            try:
                r = self._c.chat.completions.create(
                    model=self.deployment,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_completion_tokens=self.max_completion_tokens,
                    response_format={"type": "json_object"},
                )
                content = r.choices[0].message.content or ""
                return json.loads(content)
            except Exception as e:  # noqa: BLE001 - retry transient API/JSON errors
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM.json failed after {retries} tries: {last}")

    def rate_batch(self, system: str, user: str, retries: int = 3) -> Dict[str, int]:
        """Convenience for the judge: expects {"ratings": {id: grade}} JSON."""
        out = self.json(system, user, retries=retries)
        ratings = out.get("ratings", out)
        return {str(k): int(v) for k, v in ratings.items()}


def preflight() -> List[str]:
    """Return a list of missing required env vars (empty if all present)."""
    return [
        k
        for k in (
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_API_VERSION",
            "AZURE_OPENAI_DEPLOYMENT",
        )
        if not os.getenv(k)
    ]
