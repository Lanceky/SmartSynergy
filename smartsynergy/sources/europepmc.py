"""Europe PMC REST adapter.

Docs: https://europepmc.org/RestfulWebService
Copyright/reuse: https://europepmc.org/Copyright - free access is not permission to
redistribute all text, so only CC-BY/CC0 abstracts are ever written to fixtures.

Incremental runs filter on BOTH FIRST_IDATE (first indexed) and UPDATE_DATE (record
changed), because publication date alone misses late indexing and corrections.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from smartsynergy.schemas import SourceRecord
from smartsynergy.sources.base import HttpClient, SearchResult, SourceError

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TAGS = re.compile(r"<[^>]+>")

# Words that, in a title, suggest the record is a correction/retraction notice.
_CORRECTION_HINTS = ("retract", "correction", "erratum", "expression of concern", "withdrawn")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _TAGS.sub("", html.unescape(text)).strip()


class EuropePMC:
    def __init__(self, user_agent: str, cache_dir: Path | str = ".cache/http") -> None:
        self.http = HttpClient(user_agent, cache_dir=cache_dir, min_interval_s=0.2)

    # ---------------------------------------------------------------- query building
    @staticmethod
    def build_query(
        gene: str,
        variant: str,
        disease_terms: list[str],
        compound_terms_a: list[str],
        compound_terms_b: list[str],
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        def any_of(terms: list[str]) -> str:
            uniq = [t for t in dict.fromkeys(t.strip() for t in terms if t and t.strip())]
            return "(" + " OR ".join(f'"{t}"' for t in uniq) + ")"

        parts = [
            f'({gene} AND "{variant}")',
            any_of(disease_terms),
            any_of(compound_terms_a),
            any_of(compound_terms_b),
        ]
        query = " AND ".join(parts)
        if since:
            end = until or "2099-12-31"
            # Either newly indexed or recently updated: catches corrections on older papers.
            query += f' AND (FIRST_IDATE:[{since} TO {end}] OR UPDATE_DATE:[{since} TO {end}])'
        return query

    # ---------------------------------------------------------------- search
    def search(
        self, query: str, *, max_records: int = 25, page_size: int = 25, use_cache: bool = True
    ) -> SearchResult:
        records: list[SourceRecord] = []
        cursor = "*"
        total = 0
        try:
            while len(records) < max_records:
                data = self.http.get_json(
                    f"{BASE}/search",
                    {
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": min(page_size, max_records - len(records)),
                        "cursorMark": cursor,
                        "sort": "P_PDATE_D desc",
                    },
                    use_cache=use_cache,
                )
                total = int(data.get("hitCount", 0))
                page = data.get("resultList", {}).get("result", [])
                if not page:
                    break
                records.extend(self._to_record(r) for r in page)
                next_cursor = data.get("nextCursorMark")
                if not next_cursor or next_cursor == cursor:
                    cursor = None
                    break
                cursor = next_cursor
        except SourceError as exc:
            return SearchResult(records=records, coverage="failed", note=str(exc), total_hits=total)

        truncated = cursor is not None and total > len(records)
        return SearchResult(
            records=records[:max_records],
            coverage="truncated" if truncated else "complete",
            next_cursor=cursor if truncated else None,
            total_hits=total,
            note=f"{len(records)} of {total} hits retrieved" if truncated else "",
        )

    # ---------------------------------------------------------------- mapping
    @staticmethod
    def _to_record(raw: dict) -> SourceRecord:
        pmid = raw.get("pmid")
        pmcid = raw.get("pmcid")
        doi = raw.get("doi")
        source_id = pmid or pmcid or doi or f"{raw.get('source')}:{raw.get('id')}"
        title = _clean(raw.get("title"))
        abstract = _clean(raw.get("abstractText")) or None

        if raw.get("isOpenAccess") == "Y":
            access = "open_access"
        elif abstract:
            access = "abstract_only"
        else:
            access = "metadata_only"

        pub_types = (raw.get("pubTypeList") or {}).get("pubType") or []
        blob = f"{title} {' '.join(pub_types)}".lower()
        is_corrected = True if any(h in blob for h in _CORRECTION_HINTS) else None

        url = (
            f"https://europepmc.org/article/{raw.get('source', 'MED')}/{raw.get('id')}"
            if raw.get("id")
            else f"https://doi.org/{doi}"
        )
        rec = SourceRecord(
            source_id=str(source_id),
            pmid=pmid,
            pmcid=pmcid,
            doi=doi,
            title=title,
            journal=raw.get("journalTitle"),
            year=raw.get("pubYear"),
            first_index_date=raw.get("firstIndexDate"),
            url=url,
            access_level=access,
            license=raw.get("license"),
            pub_types=[str(p) for p in pub_types],
            cited_by_count=int(raw.get("citedByCount") or 0),
            abstract=abstract,
            is_corrected=is_corrected,
        )
        rec.content_hash = rec.compute_hash()
        return rec

    def close(self) -> None:
        self.http.close()
