"""
Cognee — vendored KG-based semantic memory (trimmed).

Attribution: https://github.com/topoteretes/cognee (Apache-2.0)
This copy retains only the KG store, entity extraction, and semantic query
modules needed by the MyDude.io governance stack.
"""

from .graph import KnowledgeGraph
from .extractor import EntityExtractor, extract_entities_and_relations
from .query import SemanticQuery

__all__ = ["KnowledgeGraph", "EntityExtractor", "extract_entities_and_relations", "SemanticQuery"]
