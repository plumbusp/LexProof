"""Gemini (via its OpenAI-compatible endpoint): find every legal reference.
Works with any OpenAI-compatible provider; see .env.example."""
import json
from typing import Literal

from pydantic import BaseModel

from .config import settings


class Ref(BaseModel):
    quote: str
    kind: Literal["explicit", "implicit"]
    act_name: str
    provision: str
    claim: str


class Refs(BaseModel):
    asks: list[str]
    references: list[Ref]


PROMPT = """You read requests and complaints sent to UK public bodies and find every legal reference.

- explicit: the text names a law or rule, e.g. "FOIA s.10", "section 1 of the Freedom of Information Act 2000",
  "the Banking Consumer Protection Act 2019" (include laws that may not exist; do not judge).
- implicit: the text asserts a legal duty, right or deadline without naming a law,
  e.g. "the council must reply in 2 weeks", "you are not allowed to charge me".

For each reference return:
- quote: the exact words from the text (copy characters exactly, shortest span that contains the reference
  and what is claimed about it)
- kind: explicit | implicit
- act_name: official title with year if you can tell (e.g. "Freedom of Information Act 2000"); for implicit, ""
- provision: e.g. "section 10", "regulation 5", "article 15", or ""
- claim: one short sentence stating what the writer says the law requires

Also return asks: up to 3 short plain sentences for the officer saying what the writer is asking for and what they
assert (e.g. "Asks for the contract and scoring documents for X", "Claims a 10-day deadline").

Return JSON only: {"asks": [...], "references": [...]}"""


def _schema():
    s = Refs.model_json_schema()

    def strict(n):
        if isinstance(n, dict):
            if n.get("type") == "object" and "properties" in n:
                n["additionalProperties"] = False
                n["required"] = list(n["properties"])
            for v in n.values():
                strict(v)
        elif isinstance(n, list):
            for v in n:
                strict(v)
    strict(s)
    return s


def extract(text: str) -> Refs:
    from openai import OpenAI
    kw = {"api_key": settings.ai_api_key, "timeout": 120, "max_retries": 2}
    if settings.ai_base_url:
        kw["base_url"] = settings.ai_base_url
    client = OpenAI(**kw)
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]
    last = None
    for fmt in ({"type": "json_schema", "json_schema": {"name": "refs", "schema": _schema(), "strict": True}},
                {"type": "json_object"}):
        try:
            resp = client.chat.completions.create(model=settings.ai_model, messages=messages, response_format=fmt)
            content = resp.choices[0].message.content or ""
            return Refs.model_validate(json.loads(content))
        except Exception as e:  # provider rejected the format or returned bad JSON: try the next format
            last = e
    raise RuntimeError(f"Gemini extraction failed: {str(last)[:300]}")
