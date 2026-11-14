"""Text helpers: normalisation with an offset map back to the original, sentence
spans, and the verbatim-quote check used to validate anything the AI says."""
import re

_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", " ": " ", "‑": "-"})


def normalise_with_map(s: str):
    """Lowercase, unify quotes/dashes, collapse whitespace. Returns (norm, idx)
    where idx[i] is the original index of norm[i]."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(s.translate(_TRANS)):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch.lower())
        idx.append(i)
    return "".join(out), idx


def normalise(s: str) -> str:
    return normalise_with_map(s)[0].strip()


def find_span(haystack: str, needle: str):
    """Locate needle in haystack, tolerant of quotes/whitespace/case. Returns
    (start, end) in original haystack offsets, or None."""
    if not needle:
        return None
    i = haystack.find(needle)
    if i >= 0:
        return i, i + len(needle)
    h, hmap = normalise_with_map(haystack)
    n = normalise(needle)
    j = h.find(n)
    if j < 0 or not n:
        return None
    return hmap[j], hmap[j + len(n) - 1] + 1


def is_verbatim(quote: str, source: str) -> bool:
    return bool(quote.strip()) and normalise(quote) in normalise(source)


_SENT_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z(\"'])|\n\s*\n")
_ABBREV = re.compile(r"\b(?:s|ss|reg|regs|art|para|no|e\.g|i\.e|cf|v|plc|ltd|st|mr|mrs|ms|dr)\.$", re.I)


def sentence_spans(text: str):
    spans, start = [], 0
    for m in _SENT_END.finditer(text):
        if _ABBREV.search(text[start:m.start()]):
            continue
        spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]
