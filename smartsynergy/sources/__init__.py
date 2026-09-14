"""Source adapters. Plain HTTP + caching + explicit coverage. No LLM involvement here."""

from smartsynergy.sources.base import SearchResult, SourceError
from smartsynergy.sources.europepmc import EuropePMC
from smartsynergy.sources.pubchem import PubChem

__all__ = ["EuropePMC", "PubChem", "SearchResult", "SourceError"]
