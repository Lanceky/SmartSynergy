"""PubChem PUG REST adapter: name -> CID, canonical title, synonyms.

Docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
Identity data alone establishes nothing about mechanism, approval, efficacy, or safety.
PubChem asks for <= 5 requests/second; we stay well under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from smartsynergy.sources.base import HttpClient, SourceError

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
_CAS = re.compile(r"^\d{2,7}-\d{2}-\d$")


@dataclass
class CompoundIdentity:
    query_name: str
    cid: int | None = None
    title: str = ""
    molecular_formula: str = ""
    synonyms: list[str] = field(default_factory=list)
    resolved: bool = False
    note: str = ""

    @property
    def search_terms(self) -> list[str]:
        """Names worth putting in a literature query (drops CAS numbers and noise)."""
        terms = [self.title or self.query_name]
        for syn in self.synonyms:
            if len(terms) >= 4:
                break
            s = syn.strip()
            if not s or _CAS.match(s) or len(s) > 30 or s.lower() in {t.lower() for t in terms}:
                continue
            if any(ch.isdigit() for ch in s) and len(s) < 12:  # code names like AMG 510, MRTX849
                terms.append(s)
            elif s.isalpha() and len(s) > 4:
                terms.append(s)
        return terms


class PubChem:
    def __init__(self, user_agent: str, cache_dir: Path | str = ".cache/http") -> None:
        self.http = HttpClient(user_agent, cache_dir=cache_dir, min_interval_s=0.25)

    def resolve(self, name: str) -> CompoundIdentity:
        ident = CompoundIdentity(query_name=name)
        try:
            cids = self.http.get_json(f"{BASE}/name/{name}/cids/JSON")
            cid_list = cids.get("IdentifierList", {}).get("CID", [])
            if not cid_list:
                ident.note = "no CID for this name"
                return ident
            ident.cid = int(cid_list[0])

            props = self.http.get_json(
                f"{BASE}/cid/{ident.cid}/property/Title,MolecularFormula/JSON"
            )
            row = (props.get("PropertyTable", {}).get("Properties") or [{}])[0]
            ident.title = row.get("Title", "") or name
            ident.molecular_formula = row.get("MolecularFormula", "")

            syn = self.http.get_json(f"{BASE}/cid/{ident.cid}/synonyms/JSON")
            info = (syn.get("InformationList", {}).get("Information") or [{}])[0]
            ident.synonyms = [str(s) for s in (info.get("Synonym") or [])][:25]
            ident.resolved = True
        except SourceError as exc:
            ident.note = f"lookup failed: {exc}"
        return ident

    def close(self) -> None:
        self.http.close()
