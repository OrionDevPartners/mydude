"""
EntityExtractor — extracts entities and relations from text for the KG.

Adapted from Cognee's extraction pipeline (Apache-2.0).
Uses pattern-based NER (no external model dependency) so the app remains
self-contained. The extractor is intentionally conservative: it prefers
precision over recall to avoid polluting the knowledge graph with noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


_ENTITY_PATTERNS = [
    (re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b"), "proper_noun"),
    (re.compile(r"\b((?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day)\b", re.I), "weekday"),
    (re.compile(r"\b(\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)\b"), "date"),
    (re.compile(r"\b(\d{1,2}:\d{2}(?:\s*[AP]M)?)\b", re.I), "time"),
    (re.compile(r"\b(\$[\d,]+(?:\.\d{2})?|\d+\s*(?:dollars?|USD|EUR|GBP))\b", re.I), "money"),
    (re.compile(r"\b(\d+(?:\.\d+)?%)\b"), "percentage"),
    (re.compile(r"\bhttps?://\S+"), "url"),
    (re.compile(r"\b([A-Z]{2,}(?:_[A-Z]+)*)\b"), "acronym"),
]

_RELATION_PATTERNS = [
    (re.compile(r"(\w[\w\s]{1,30})\s+(?:is|are|was|were)\s+(\w[\w\s]{1,30})", re.I), "is_a"),
    (re.compile(r"(\w[\w\s]{1,30})\s+(?:causes?|caused)\s+(\w[\w\s]{1,30})", re.I), "causes"),
    (re.compile(r"(\w[\w\s]{1,30})\s+(?:requires?|requires|needs?)\s+(\w[\w\s]{1,30})", re.I), "requires"),
    (re.compile(r"(\w[\w\s]{1,30})\s+(?:before|after|during)\s+(\w[\w\s]{1,30})", re.I), "temporal"),
    (re.compile(r"deadline\s+(?:is|:)\s+(\w[\w\s]{1,20})", re.I), None),
    (re.compile(r"(?:finish|complete|done)\s+by\s+(\w[\w\s]{1,20})", re.I), None),
]

_STOP_ENTITIES = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She",
    "They", "We", "You", "I", "My", "Our", "Your", "His", "Her", "Its",
    "Then", "When", "Where", "How", "What", "Who", "Which",
}


@dataclass
class ExtractedEntity:
    text: str
    entity_type: str
    start: int
    end: int


@dataclass
class ExtractedRelation:
    subject: str
    predicate: str
    obj: str


@dataclass
class ExtractionResult:
    entities: List[ExtractedEntity] = field(default_factory=list)
    relations: List[ExtractedRelation] = field(default_factory=list)
    raw_text: str = ""


class EntityExtractor:
    """Pattern-based entity and relation extractor."""

    def extract(self, text: str) -> ExtractionResult:
        entities: List[ExtractedEntity] = []
        seen: Set[str] = set()

        for pattern, etype in _ENTITY_PATTERNS:
            for m in pattern.finditer(text):
                entity_text = m.group(0).strip()
                if entity_text in _STOP_ENTITIES or len(entity_text) < 2:
                    continue
                key = entity_text.lower()
                if key not in seen:
                    seen.add(key)
                    entities.append(ExtractedEntity(
                        text=entity_text,
                        entity_type=etype,
                        start=m.start(),
                        end=m.end(),
                    ))

        relations: List[ExtractedRelation] = []
        for pattern, pred in _RELATION_PATTERNS:
            for m in pattern.finditer(text):
                groups = m.groups()
                if len(groups) >= 2 and pred:
                    subj = groups[0].strip()
                    obj = groups[1].strip()
                    if subj and obj and subj not in _STOP_ENTITIES:
                        relations.append(ExtractedRelation(
                            subject=subj, predicate=pred, obj=obj,
                        ))
                elif len(groups) >= 1 and pred is None:
                    obj = groups[0].strip()
                    verb = m.group(0).lower()
                    if "deadline" in verb:
                        relations.append(ExtractedRelation("task", "has_deadline", obj))
                    elif "finish" in verb or "complete" in verb or "done" in verb:
                        relations.append(ExtractedRelation("task", "complete_by", obj))

        return ExtractionResult(entities=entities, relations=relations, raw_text=text)


def extract_entities_and_relations(text: str) -> ExtractionResult:
    return EntityExtractor().extract(text)
