import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


NEGATION_WORDS = {
    "not", "no", "never", "neither", "nor", "none", "nothing",
    "nowhere", "hardly", "barely", "scarcely", "doesn't", "don't",
    "didn't", "isn't", "aren't", "wasn't", "weren't", "won't",
    "wouldn't", "shouldn't", "couldn't", "cannot", "can't",
    "without", "lack", "lacks", "lacking", "absent", "false",
    "incorrect", "wrong", "invalid", "impossible", "unlikely",
}

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "but", "and", "or",
    "if", "while", "about", "up", "that", "this", "it", "its",
    "i", "me", "my", "we", "our", "you", "your", "he", "him",
    "his", "she", "her", "they", "them", "their", "what", "which",
    "who", "whom",
}


@dataclass
class ClaimProvenance:
    claim_id: str
    origin_provider: str
    origin_role: str
    wave_idx: int
    evidence_pointers: List[str] = field(default_factory=list)
    transformations: List[Dict] = field(default_factory=list)
    parent_claim_ids: List[str] = field(default_factory=list)
    hr_at_creation: float = 0.0
    cs_at_creation: float = 100.0


@dataclass
class ConsistencyResult:
    consistent: bool
    conflicting_claims: List[Dict]
    similarity_score: float
    details: str = ""


def _extract_keywords(text: str) -> set:
    try:
        words = text.lower().split()
        cleaned = set()
        for w in words:
            stripped = "".join(c for c in w if c.isalnum())
            if stripped and len(stripped) > 2 and stripped not in STOP_WORDS:
                cleaned.add(stripped)
        return cleaned
    except Exception:
        return set()


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    try:
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0
    except Exception:
        return 0.0


def _has_negation_near_keywords(text: str, keywords: set) -> bool:
    try:
        words = text.lower().split()
        for i, w in enumerate(words):
            stripped = "".join(c for c in w if c.isalnum())
            if stripped in NEGATION_WORDS:
                window = words[max(0, i - 3):i + 4]
                window_cleaned = {
                    "".join(c for c in ww if c.isalnum()) for ww in window
                }
                if window_cleaned & keywords:
                    return True
        return False
    except Exception:
        return False


class ProvenanceTree:
    def __init__(self) -> None:
        self._records: Dict[str, ClaimProvenance] = {}

    def add_provenance(
        self,
        claim_id: str,
        provider: str,
        role: str,
        wave: int,
        evidence: Optional[List[str]] = None,
        parent_ids: Optional[List[str]] = None,
        hr: float = 0.0,
        cs: float = 100.0,
    ) -> ClaimProvenance:
        try:
            prov = ClaimProvenance(
                claim_id=claim_id,
                origin_provider=provider,
                origin_role=role,
                wave_idx=wave,
                evidence_pointers=evidence or [],
                parent_claim_ids=parent_ids or [],
                hr_at_creation=max(0.0, min(1.0, hr)),
                cs_at_creation=max(0.0, min(100.0, cs)),
            )
            self._records[claim_id] = prov
            return prov
        except Exception:
            fallback = ClaimProvenance(
                claim_id=claim_id,
                origin_provider=provider or "unknown",
                origin_role=role or "unknown",
                wave_idx=wave if isinstance(wave, int) else 0,
            )
            self._records[claim_id] = fallback
            return fallback

    def record_transformation(
        self, claim_id: str, role: str, action: str
    ) -> bool:
        try:
            prov = self._records.get(claim_id)
            if prov is None:
                return False
            prov.transformations.append({
                "role": role,
                "action": action,
                "timestamp": time.time(),
            })
            return True
        except Exception:
            return False

    def get_lineage(self, claim_id: str) -> List[str]:
        try:
            lineage: List[str] = []
            visited: set = set()
            queue = [claim_id]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                prov = self._records.get(current)
                if prov is None:
                    continue
                for parent_id in prov.parent_claim_ids:
                    if parent_id not in visited:
                        lineage.append(parent_id)
                        queue.append(parent_id)
            return lineage
        except Exception:
            return []

    def get_risk_path(self, claim_id: str) -> List[Dict]:
        try:
            path: List[Dict] = []
            prov = self._records.get(claim_id)
            if prov is None:
                return path
            path.append({
                "claim_id": prov.claim_id,
                "provider": prov.origin_provider,
                "role": prov.origin_role,
                "wave": prov.wave_idx,
                "hr": prov.hr_at_creation,
                "cs": prov.cs_at_creation,
            })
            ancestors = self.get_lineage(claim_id)
            for ancestor_id in ancestors:
                ancestor = self._records.get(ancestor_id)
                if ancestor:
                    path.append({
                        "claim_id": ancestor.claim_id,
                        "provider": ancestor.origin_provider,
                        "role": ancestor.origin_role,
                        "wave": ancestor.wave_idx,
                        "hr": ancestor.hr_at_creation,
                        "cs": ancestor.cs_at_creation,
                    })
            return path
        except Exception:
            return []

    def find_high_risk_origins(self) -> List[ClaimProvenance]:
        try:
            return [
                prov for prov in self._records.values()
                if prov.hr_at_creation > 0.5
            ]
        except Exception:
            return []

    def to_summary(self, limit: int = 10) -> str:
        try:
            total = len(self._records)
            if total == 0:
                return "ProvenanceTree: empty (0 claims tracked)"
            high_risk = self.find_high_risk_origins()
            lines = [
                f"ProvenanceTree: {total} claims tracked, {len(high_risk)} high-risk",
            ]
            shown = 0
            for cid, prov in self._records.items():
                if shown >= limit:
                    lines.append(f"  ... and {total - shown} more")
                    break
                parents = ",".join(prov.parent_claim_ids) if prov.parent_claim_ids else "root"
                lines.append(
                    f"  {cid}: provider={prov.origin_provider} role={prov.origin_role} "
                    f"wave={prov.wave_idx} hr={prov.hr_at_creation:.2f} "
                    f"cs={prov.cs_at_creation:.0f} parents=[{parents}] "
                    f"transforms={len(prov.transformations)}"
                )
                shown += 1
            return "\n".join(lines)
        except Exception:
            return "ProvenanceTree: error generating summary"

    def get(self, claim_id: str) -> Optional[ClaimProvenance]:
        return self._records.get(claim_id)


class ConsistencyChecker:
    def __init__(self) -> None:
        self._verified_facts: List[Dict] = []

    def add_verified(
        self,
        text: str,
        claim_id: str,
        confidence: float = 1.0,
        source: str = "",
    ) -> None:
        try:
            self._verified_facts.append({
                "text": text,
                "claim_id": claim_id,
                "confidence": max(0.0, min(1.0, confidence)),
                "source": source,
                "keywords": _extract_keywords(text),
            })
        except Exception:
            self._verified_facts.append({
                "text": text,
                "claim_id": claim_id,
                "confidence": 0.5,
                "source": source,
                "keywords": set(),
            })

    def check_consistency(self, new_claim_text: str) -> ConsistencyResult:
        try:
            if not self._verified_facts:
                return ConsistencyResult(
                    consistent=True,
                    conflicting_claims=[],
                    similarity_score=0.0,
                    details="No verified facts to check against.",
                )

            new_keywords = _extract_keywords(new_claim_text)
            if not new_keywords:
                return ConsistencyResult(
                    consistent=True,
                    conflicting_claims=[],
                    similarity_score=0.0,
                    details="New claim has no significant keywords.",
                )

            max_similarity = 0.0
            conflicting: List[Dict] = []

            for fact in self._verified_facts:
                fact_keywords = fact.get("keywords", set())
                sim = _jaccard_similarity(new_keywords, fact_keywords)
                max_similarity = max(max_similarity, sim)

                if sim > 0.15 and _has_negation_near_keywords(new_claim_text, fact_keywords):
                    conflicting.append({
                        "claim_id": fact["claim_id"],
                        "text": fact["text"],
                        "confidence": fact["confidence"],
                        "similarity": round(sim, 3),
                    })

            consistent = len(conflicting) == 0
            details_parts = []
            if conflicting:
                details_parts.append(
                    f"Found {len(conflicting)} potential contradiction(s) with verified facts."
                )
            else:
                details_parts.append("No contradictions detected.")
            details_parts.append(f"Max similarity: {max_similarity:.3f}")

            return ConsistencyResult(
                consistent=consistent,
                conflicting_claims=conflicting,
                similarity_score=round(max_similarity, 3),
                details=" ".join(details_parts),
            )
        except Exception:
            return ConsistencyResult(
                consistent=True,
                conflicting_claims=[],
                similarity_score=0.0,
                details="Error during consistency check; defaulting to consistent.",
            )

    def get_verified_context(
        self, query: str, limit: int = 5
    ) -> List[Dict]:
        try:
            if not self._verified_facts:
                return []

            query_keywords = _extract_keywords(query)
            if not query_keywords:
                return self._verified_facts[:limit]

            scored = []
            for fact in self._verified_facts:
                fact_keywords = fact.get("keywords", set())
                sim = _jaccard_similarity(query_keywords, fact_keywords)
                scored.append((sim, fact))

            scored.sort(key=lambda x: x[0], reverse=True)
            results = []
            for sim, fact in scored[:limit]:
                results.append({
                    "text": fact["text"],
                    "claim_id": fact["claim_id"],
                    "confidence": fact["confidence"],
                    "source": fact["source"],
                    "relevance": round(sim, 3),
                })
            return results
        except Exception:
            return []
