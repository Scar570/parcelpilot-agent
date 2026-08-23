"""
Document search over the markdown source docs.

Design choice: BM25 keyword search over a corpus of 6 short documents,
rather than an embeddings/vector DB. At this corpus size a vector store adds
infra and cost with no retrieval-quality benefit; BM25 is deterministic,
needs no external API call, and is trivial to explain and to unit-test. The
architecture note calls this out as a trade-off that would need revisiting
if the document set grew into the hundreds.

Each chunk carries its source_tier + status metadata (parsed from the YAML
front matter written into each doc). The precedence/authority logic lives
downstream in the agent's system prompt + the policy_rules calculator --
this module's job is just to retrieve relevant, correctly-labeled text.
"""
import re
from pathlib import Path
from rank_bm25 import BM25Okapi
from .config import DOCS_DIR

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text):
    return _TOKEN_RE.findall(text.lower())


def _parse_front_matter(raw_text):
    """Very small YAML front-matter parser (key: value pairs only, no
    nesting) -- sufficient for our doc set and avoids a PyYAML dependency."""
    meta = {}
    body = raw_text
    if raw_text.startswith("---"):
        end = raw_text.find("\n---", 3)
        if end != -1:
            fm_block = raw_text[3:end].strip()
            body = raw_text[end + 4:].lstrip("\n")
            for line in fm_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    v = v.strip()
                    if v in ("true", "false"):
                        v = v == "true"
                    meta[k.strip()] = v
    return meta, body


def _load_corpus():
    docs = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        raw = path.read_text()
        meta, body = _parse_front_matter(raw)
        # chunk by section (## headings) so results are focused, not whole-doc
        sections = re.split(r"\n(?=## )", body)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            docs.append({
                "doc_id": meta.get("doc_id", path.stem),
                "title": meta.get("title", path.stem),
                "status": meta.get("status", "UNKNOWN"),
                "source_tier": meta.get("source_tier", "unknown"),
                "effective_date": meta.get("effective_date", ""),
                "account_id": meta.get("account_id"),
                "text": section,
            })
    return docs


_CORPUS = _load_corpus()
_TOKENIZED = [_tokenize(d["text"]) for d in _CORPUS]
_BM25 = BM25Okapi(_TOKENIZED)


def search_documents(query, top_k=4, include_deprecated=False, account_id=None):
    """Returns the top_k most relevant chunks.

    include_deprecated: False by default -- this is the main lever that
    keeps the deprecated v2 policy out of ordinary answers. It can only be
    flipped by the agent when the user explicitly asks about historical /
    old / superseded policy, and even then results are clearly labeled.

    account_id: if provided, contract chunks belonging to OTHER accounts are
    filtered out. This is a soft relevance filter (not a security boundary
    -- contracts contain no other-customer PII beyond the account's own
    terms -- but it keeps a customer's search results from surfacing another
    customer's contract clauses at all).
    """
    tokens = _tokenize(query)
    scores = _BM25.get_scores(tokens)
    ranked = sorted(range(len(_CORPUS)), key=lambda i: scores[i], reverse=True)

    results = []
    for i in ranked:
        doc = _CORPUS[i]
        if scores[i] <= 0:
            break
        if not include_deprecated and doc["source_tier"] == "deprecated":
            continue
        if doc["source_tier"] == "contract" and account_id is not None:
            if doc.get("account_id") and doc["account_id"] != account_id:
                continue
        results.append({**doc, "_score": round(float(scores[i]), 3)})
        if len(results) >= top_k:
            break
    return results
