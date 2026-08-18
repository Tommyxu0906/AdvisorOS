"""Fetching filings from EDGAR, politely and once.

SEC publishes this data for exactly this purpose and asks two things in return: identify
yourself in the User-Agent, and stay under ten requests a second. Both are honoured here, and
the rate limit is deliberately set well below the ceiling — a backfill of forty quarters is not
urgent, and there is no version of this project where hammering a public service is worth it.

Everything fetched goes straight to the immutable raw layer and is never fetched twice. A
resumed backfill reads what is already on disk, which makes the whole thing restartable and
means a parser change costs nothing at SEC.

One structural detail worth knowing before reading the code: EDGAR's `submissions` endpoint
returns only *recent* filings inline — for Berkshire that reaches back to late 2016 — and older
ones live in separate archive files listed under `filings.files`. Any backfill claiming to start
before that boundary has to follow those, and a run that silently stops at the boundary while
reporting the requested range is exactly the overstatement `DatasetVersion.coverage` exists to
prevent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.distillation.finance_nuwa.store import AmendmentType, FilingRef

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
ARCHIVE_URL = "https://data.sec.gov/submissions/{filename}"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodashes}/"
DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodashes}/{filename}"

# SEC asks for under 10/s. Well under it: a forty-quarter backfill takes under a minute either
# way, and the courtesy costs nothing.
MIN_SECONDS_BETWEEN_REQUESTS = 0.2

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class EdgarClient:
    """Rate-limited, identified HTTP against EDGAR.

    `user_agent` must carry a real contact address — SEC's access policy requires it, and
    requests without one get blocked rather than throttled.
    """

    user_agent: str
    min_interval: float = MIN_SECONDS_BETWEEN_REQUESTS

    def get(self, url: str) -> bytes:
        _throttle(self.min_interval)
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": self.user_agent})
        if response.status_code != 200:
            raise EdgarError(f"{url} returned {response.status_code}")
        return response.content

    def list_filings(self, cik: str, *, forms: tuple[str, ...] = ("13F-HR",)) -> list[FilingRef]:
        """Every matching filing, following the archive files for older periods.

        Returns them sorted by period. Whether the archives were reachable is visible in the
        result — a caller comparing the earliest period against what it asked for learns
        immediately that the older half is missing, rather than discovering it in a score.
        """
        import json

        payload = json.loads(self.get(SUBMISSIONS_URL.format(cik=cik)))
        refs = _refs_from_block(payload.get("filings", {}).get("recent", {}), forms)

        for archive in payload.get("filings", {}).get("files", []):
            name = archive.get("name")
            if not name:
                continue
            older = json.loads(self.get(ARCHIVE_URL.format(filename=name)))
            # Archive files hold the same column-oriented block, at the top level.
            refs.extend(_refs_from_block(older, forms))

        return sorted(refs, key=lambda r: (r.period_end, r.filed_at))

    def information_table_name(self, cik: str, accession: str) -> str | None:
        """Find the information table document inside a filing's directory.

        The filename is not stable across a decade — `form13fInfoTable.xml` in 2017, a bare
        numeric name in 2026 — so it is discovered rather than assumed.
        """
        import re

        index = self.get(
            FILING_INDEX_URL.format(cik=cik, accession_nodashes=_nodashes(accession))
        ).decode("utf-8", "replace")
        candidates = [
            name
            for name in re.findall(r'href="[^"]*?/([A-Za-z0-9_.\-]+\.xml)"', index)
            if name != "primary_doc.xml"
        ]
        return candidates[0] if candidates else None

    def document(self, cik: str, accession: str, filename: str) -> bytes:
        return self.get(
            DOCUMENT_URL.format(cik=cik, accession_nodashes=_nodashes(accession), filename=filename)
        )


class EdgarError(RuntimeError):
    """A fetch failed. Carries no credential because there is none to carry."""


def amendment_type_from_primary_doc(xml_text: str) -> AmendmentType:
    """Read whether an amendment restates or supplements.

    A restatement replaces the original outright; an amendment adding holdings has to be
    combined with it. Getting this backwards either drops real positions or double-counts them,
    so an unreadable value becomes `unknown` and the quarter goes to review.
    """
    import re

    match = re.search(r"<amendmentType>\s*([^<]+?)\s*</amendmentType>", xml_text, re.IGNORECASE)
    if not match:
        return AmendmentType.unknown
    value = match.group(1).strip().upper()
    if "RESTATEMENT" in value:
        return AmendmentType.restatement
    if "NEW HOLDING" in value:
        return AmendmentType.new_holdings
    return AmendmentType.unknown


_last_request_at = 0.0


def _throttle(min_interval: float) -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_at = time.monotonic()


def _refs_from_block(block: dict, forms: tuple[str, ...]) -> list[FilingRef]:
    """EDGAR returns filings column-oriented: parallel lists rather than a list of records."""
    form_list = block.get("form") or []
    refs: list[FilingRef] = []
    for index, form in enumerate(form_list):
        if not any(form.startswith(prefix) for prefix in forms):
            continue
        report = _as_date(block.get("reportDate", [None] * len(form_list))[index])
        filed = _as_date(block.get("filingDate", [None] * len(form_list))[index])
        accession = block.get("accessionNumber", [None] * len(form_list))[index]
        if not report or not filed or not accession:
            continue
        refs.append(
            FilingRef(
                accession=accession,
                form_type=form,
                period_end=report,
                filed_at=filed,
            )
        )
    return refs


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _nodashes(accession: str) -> str:
    return accession.replace("-", "")
