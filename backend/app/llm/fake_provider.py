"""
FakeLLMProvider: deterministic, offline, zero-cost.

This is not a mock of the interface - it is a genuine, if simple,
implementation. It really parses text with regexes tuned to the vendor
contract/amendment/invoice domain, really produces embeddings (a hashed
bag-of-words vector, so documents that share vocabulary really do land
closer together in cosine similarity), and really evaluates the numeric
threshold rules used in this project's fixtures. That is what makes it
legitimate to test *behavior* against - crash-resume, concurrency, and
prompt-injection resistance - and not just "does the mock get called".

It intentionally does not attempt general-purpose language understanding.
For an arbitrary rule in unrestricted prose, OpenAIProvider is the real
implementation; this one is scoped to what the fixtures exercise, and that
scoping is documented, not hidden.
"""

from __future__ import annotations

import hashlib
import math
import re
import time

from app.domain.entities import (
    ClassificationResult,
    CostRecord,
    ExtractedFact,
    RuleEvaluation,
)
from app.llm.provider import UNTRUSTED_PREAMBLE

EMBEDDING_DIM = 1536

_FACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("payment_terms_days", re.compile(r"(?:net|payment\s+(?:due|terms)(?:\s+within)?)\s*(\d{1,3})\s*days?", re.I)),
    ("termination_notice_days", re.compile(r"terminat\w*[^.]{0,60}?(\d{1,3})\s*days?\s*(?:written\s*)?notice", re.I)),
    ("contract_value", re.compile(r"(?:total\s+)?(?:contract\s+value|not\s+to\s+exceed)[^\d$]{0,80}\$\s?([\d,]+(?:\.\d{2})?)", re.I)),
    ("indemnification_cap_usd", re.compile(r"indemnif\w*[^.]{0,80}?\$\s?([\d,]+(?:\.\d{2})?)", re.I)),
    ("governing_law", re.compile(r"governed by(?: the)? laws? of (?:the )?(?:state of )?([A-Za-z ]+?)[.,;]", re.I)),
    ("effective_date", re.compile(r"effective\s+(?:date|as of)[:\s]*([A-Za-z]+ \d{1,2},? \d{4})", re.I)),
    ("invoice_number", re.compile(r"[Ii]nvoice\s*(?:#|[Nn]o\.?|[Nn]umber)?\s*[:\s]\s*([A-Z0-9][A-Z0-9\-]{3,19})")),
    ("invoice_amount_due", re.compile(r"amount\s+due[:\s]*\$\s?([\d,]+(?:\.\d{2})?)", re.I)),
]

_PARTY_PATTERN = re.compile(
    r'between\s+(.+?)\s*\("?Vendor"?\)\s+and\s+(.+?)\s*\("?Client"?\)', re.I | re.DOTALL
)

_TYPE_KEYWORDS = {
    "amendment": ["amendment", "hereby amended", "amends the agreement"],
    "invoice": ["invoice", "amount due", "bill to", "remit payment"],
    "contract": ["agreement", "hereby agree", "terms and conditions", "witnesseth"],
}


def _stem(word: str) -> str:
    """A deliberately crude stemmer - just enough to stop 'governing' vs
    'governed' or 'law' vs 'laws' from hashing to different dimensions
    and silently tanking similarity between a claim and its own source.
    Real embedding models handle this via learned representations; this
    fake provider needs the explicit rule."""
    if word.isdigit() or len(word) <= 3:
        return word
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


# Connective/structural words that show up in nearly every contract clause
# regardless of the specific value in dispute ("shall be governed by the
# laws of the State of ___" is identical whether ___ is Delaware or
# Texas). Dampened so they don't drown out the one word that actually
# varies - without this, two clauses differing only in a state or company
# name score deceptively similar, since ~90% of their words match.
_BOILERPLATE = {
    "shall", "the", "of", "by", "without", "regard", "its", "conflict",
    "law", "principle", "state", "this", "agreement", "party", "either",
    "other", "under", "section", "article", "be", "not", "to", "a", "an",
    "and", "or", "for", "from", "with", "is", "are", "as", "in", "that",
}


def _hash_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """A hashed, weighted bag-of-words vector - not a learned semantic
    embedding, but a genuine, deterministic similarity signal, tuned
    empirically (see docs/architecture.md) so that a short claim sentence
    and the long source paragraph it was drawn from score as similar, and
    a claim with the *wrong* value scores clearly lower than one with the
    right value - whether that value is a number (30 days vs 45 days) or
    a name (Delaware vs Texas). Numeric tokens and proper nouns (detected
    from original-case capitalization, before lowercasing) are weighted
    well above ordinary prose; common contract boilerplate is dampened.
    Both are exactly the disputed content in this domain - a wrong number
    or a wrong party/jurisdiction name can't hide behind matching
    boilerplate around it.
    """
    vec = [0.0] * dim
    raw_words = re.findall(r"[A-Za-z0-9]+", text)
    for i, raw_word in enumerate(raw_words):
        lowered = raw_word.lower()
        w = _stem(lowered)
        is_sentence_start = i == 0 or raw_words[i - 1].endswith((".", "!", "?"))
        looks_proper = raw_word[:1].isupper() and not is_sentence_start and lowered not in _BOILERPLATE
        if w.isdigit():
            weight = 4.0
        elif looks_proper:
            weight = 3.0
        elif w in _BOILERPLATE:
            weight = 0.35
        else:
            weight = 1.0 + 0.2 * len(w)
        digest = hashlib.sha256(w.encode()).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _first_number(text: str) -> float | None:
    m = re.search(r"[\d,]+(?:\.\d+)?", text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


class FakeLLMProvider:
    name = "fake"
    model = "fake-offline-v1"

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], CostRecord]:
        start = time.perf_counter()
        vectors = [_hash_embed(t) for t in texts]
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = CostRecord(
            node="embed", model=self.model,
            input_tokens=sum(len(t.split()) for t in texts),
            output_tokens=0, cost_usd=0.0, latency_ms=latency_ms,
        )
        return vectors, cost

    async def classify_document(self, text: str) -> tuple[ClassificationResult, CostRecord]:
        start = time.perf_counter()
        lowered = text.lower()
        scores = {
            doc_type: sum(lowered.count(kw) for kw in kws)
            for doc_type, kws in _TYPE_KEYWORDS.items()
        }
        best_type = max(scores, key=lambda k: scores[k])
        total = sum(scores.values()) or 1
        confidence = min(0.99, 0.5 + scores[best_type] / total / 2)
        if scores[best_type] == 0:
            best_type, confidence = "unknown", 0.0
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = CostRecord(
            node="classify", model=self.model,
            input_tokens=len(text.split()), output_tokens=1,
            cost_usd=0.0, latency_ms=latency_ms,
        )
        return ClassificationResult(document_type=best_type, confidence=confidence), cost

    async def extract_facts(self, text: str) -> tuple[list[ExtractedFact], CostRecord]:
        start = time.perf_counter()
        facts: list[ExtractedFact] = []
        for fact_key, pattern in _FACT_PATTERNS:
            match = pattern.search(text)
            if match:
                facts.append(ExtractedFact(
                    fact_key=fact_key, fact_value=match.group(1).strip(),
                    source_chunk_index=0, confidence=0.9,
                ))
        party_match = _PARTY_PATTERN.search(text)
        if party_match:
            facts.append(ExtractedFact("vendor_name", _normalize_whitespace(party_match.group(1)), 0, 0.9))
            facts.append(ExtractedFact("client_name", _normalize_whitespace(party_match.group(2)), 0, 0.9))
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = CostRecord(
            node="extract", model=self.model,
            input_tokens=len(text.split()), output_tokens=len(facts) * 4,
            cost_usd=0.0, latency_ms=latency_ms,
        )
        return facts, cost

    async def evaluate_rule(self, rule_text: str, source_text: str) -> tuple[RuleEvaluation, CostRecord]:
        start = time.perf_counter()
        # Rules in this domain are phrased as numeric ceilings/floors:
        # "Payment terms must not exceed 30 days", "Indemnification cap
        # must be at least $50,000". We parse the threshold from the rule
        # and the actual value from the cited source text.
        threshold = _first_number(rule_text)
        actual = _first_number(source_text)
        rule_lower = rule_text.lower()

        if threshold is None or actual is None:
            verdict = "evidence_not_found"
            evidence = None
        elif "at least" in rule_lower or "minimum" in rule_lower:
            verdict = "passed" if actual >= threshold else "failed"
            evidence = source_text.strip()[:300]
        else:
            # default: "must not exceed" / "no more than" / bare ceiling
            verdict = "passed" if actual <= threshold else "failed"
            evidence = source_text.strip()[:300]

        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = CostRecord(
            node="rule_validation", model=self.model,
            input_tokens=len((rule_text + source_text).split()), output_tokens=8,
            cost_usd=0.0, latency_ms=latency_ms,
        )
        return RuleEvaluation(
            rule_id="", verdict=verdict, evidence_text=evidence, source_chunk_id=None,
        ), cost

    async def draft_report_claims(self, facts: list[dict]) -> tuple[list[str], CostRecord]:
        start = time.perf_counter()
        claims = []
        for f in facts:
            label = f["fact_key"].replace("_", " ")
            claims.append(f"The {label} is {f['fact_value']}.")
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = CostRecord(
            node="report_generation", model=self.model,
            input_tokens=sum(len(str(f)) for f in facts), output_tokens=len(claims) * 8,
            cost_usd=0.0, latency_ms=latency_ms,
        )
        return claims, cost


assert UNTRUSTED_PREAMBLE  # imported to keep the wrap_untrusted contract visible here too
