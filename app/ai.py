"""AI validation layer (second opinion). Provider-neutral: any OpenAI-compatible
chat-completions endpoint (OpenAI, Gemini's OpenAI endpoint, Hostinger AI Router).

The AI never sets a status. It returns an opinion per row, a short-claim
rewrite, an optional quote (kept only if it is verbatim in the fetched source),
a case-level assessment, and citations the rule extractor may have missed.
"""
import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from .config import settings

Status = Literal["VERIFIED", "VERIFIED_WITH_CAVEAT", "MISQUOTED", "NOT_FOUND", "NOT_LEGISLATION", "UNVERIFIED"]


class RowOpinion(BaseModel):
    id: str
    agree: bool
    suggested_status: Status
    reason: str
    short_claim: str
    law_quote: str
    implication: str
    weak_factual_basis: bool


class Missed(BaseModel):
    quote: str
    why: str


class Core(BaseModel):
    strength: Literal["strong", "moderate", "weak", "unclear"]
    reason: str


class Validation(BaseModel):
    rows: list[RowOpinion]
    core: Core
    missed_citations: list[Missed]


SYSTEM = """You are a second-opinion reviewer for a UK complaints caseworker.
A deterministic checker has already fetched the real legal sources and assigned a status to every legal claim
in a customer complaint. Your job is to review each row against the fetched source text supplied, and say
whether you agree.

Status meanings:
VERIFIED = provision exists and supports the claim. VERIFIED_WITH_CAVEAT = exists, but a detail is slightly off
or the claim depends on facts. MISQUOTED = exists but text contradicts the claim. NOT_FOUND = instrument/provision
does not exist, or no statutory source for the claim. NOT_LEGISLATION = a regulator rule or ombudsman practice.
UNVERIFIED = source could not be checked.

Rules:
- Base every judgement on the supplied source text. If the text is missing, say so rather than relying on memory.
- law_quote must be copied exactly from the supplied source text, or be an empty string.
- short_claim: one plain sentence (max 20 words) stating what the complainant asserts.
- implication: one sentence on what this means for handling the complaint.
- weak_factual_basis: true when the law exists but the facts described are unlikely to meet it.
- core: judge the underlying complaint on its facts, independent of citation errors. Bad citations do not make
  a complaint weak.
- missed_citations: legal or regulatory authorities in the complaint that are NOT in the rows (quote them exactly).
Return JSON only."""


def _schema():
    s = Validation.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                strict(v)
        elif isinstance(node, list):
            for v in node:
                strict(v)
    strict(s)
    return s


def _client():
    from openai import OpenAI
    kw = {"api_key": settings.ai_api_key, "timeout": 180, "max_retries": 2}
    if settings.ai_base_url:
        kw["base_url"] = settings.ai_base_url
    return OpenAI(**kw)


def _payload(text: str, claims: list[dict]) -> str:
    rows = []
    for c in claims:
        src = c["law"]["text"][:3500]
        rel = "\n\n".join(f"[{r['label']}]\n{r['text'][:2000]}" for r in c["law"]["related"])
        rows.append({"id": c["id"], "original_text": c["original_text"], "checker_status": c["status"],
                     "checker_reason": c["reason"], "source_title": c["law"]["title"],
                     "source_heading": c["law"]["heading"], "source_url": c["law"]["url"],
                     "source_text": src, "related_text": rel[:5000],
                     "nearest_real": [n["title"] for n in c["nearest_real"]]})
    return json.dumps({"complaint": text, "rows": rows}, ensure_ascii=False)


def validate(text: str, claims: list[dict]) -> Validation:
    if not settings.ai_enabled:
        raise RuntimeError("AI layer not configured (set AI_API_KEY and AI_MODEL)")
    client = _client()
    system = SYSTEM + "\n\nJSON schema:\n" + json.dumps(_schema())
    messages = [{"role": "system", "content": system}, {"role": "user", "content": _payload(text, claims)}]
    formats = [{"type": "json_schema", "json_schema": {"name": "validation", "schema": _schema(), "strict": True}},
               {"type": "json_object"}]
    last_err = None
    for fmt in formats:
        try:
            resp = client.chat.completions.create(model=settings.ai_model, messages=messages, response_format=fmt)
        except Exception as e:  # some providers reject json_schema; fall back to json_object
            last_err = e
            continue
        content = resp.choices[0].message.content or ""
        try:
            return Validation.model_validate_json(content)
        except ValidationError as e:
            last_err = e
            messages = messages + [{"role": "assistant", "content": content},
                                   {"role": "user", "content": f"Invalid JSON for the schema: {e.errors()[:3]}. "
                                                               f"Return corrected JSON only."}]
    raise RuntimeError(f"AI validation failed: {str(last_err)[:300]}")
