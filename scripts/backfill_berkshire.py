#!/usr/bin/env python
"""Fetch and normalize Berkshire's 13F history into the immutable store.

    python scripts/backfill_berkshire.py --start 2014-01-01 --end 2024-12-31

Restartable: anything already in `raw/` is read from disk rather than re-fetched, so a parser
change costs nothing at SEC and a interrupted run resumes where it stopped.

Reports actual coverage rather than the requested range. If the older archives turn out to be
unreachable or differently shaped, the run still finishes and says which quarters it holds —
a dataset that quietly claims eleven years while containing eight is worse than a short one.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.distillation.finance_nuwa.edgar import (  # noqa: E402
    EdgarClient,
    EdgarError,
    amendment_type_from_primary_doc,
)
from app.distillation.finance_nuwa.sec_13f import (  # noqa: E402
    parse_information_table,
    parse_manager_table,
)
from app.distillation.finance_nuwa.store import (  # noqa: E402
    DatasetVersion,
    FilingStore,
    resolve_quarter,
)

CIK = "1067983"
ENTITY = "Berkshire Hathaway Inc"
INFO_TABLE_FILE = "information_table.xml"
PRIMARY_DOC_FILE = "primary_doc.xml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--root", default="data/berkshire")
    parser.add_argument("--version", default="berkshire-v1.0")
    parser.add_argument(
        "--user-agent",
        default="AdvisorOS research contact: kanghuanxu@gmail.com",
        help="SEC requires a real contact address; requests without one are blocked",
    )
    args = parser.parse_args()

    start, end = _as_date(args.start), _as_date(args.end)
    store = FilingStore(Path(args.root))
    client = EdgarClient(user_agent=args.user_agent)

    print(f"==> listing 13F filings for {ENTITY}")
    try:
        refs = client.list_filings(CIK)
    except EdgarError as exc:
        print(f"    could not list filings: {exc}", file=sys.stderr)
        return 1

    in_window = [r for r in refs if start <= r.period_end <= end]
    print(f"    {len(refs)} filings on record, {len(in_window)} within {start}..{end}")
    if in_window:
        print(f"    earliest available in window: {min(r.period_end for r in in_window)}")

    # Fetch raw first, so amendment type is readable before any quarter is resolved.
    enriched = []
    for ref in in_window:
        try:
            enriched.append(_fetch(client, store, ref))
        except EdgarError as exc:
            print(f"    !! {ref.accession} ({ref.period_end}): {exc}", file=sys.stderr)

    by_period: dict[date, list] = {}
    for ref in enriched:
        by_period.setdefault(ref.period_end, []).append(ref)

    lineage = [resolve_quarter(filings) for _, filings in sorted(by_period.items())]
    canonical = {q.canonical_accession for q in lineage}
    print(f"\n==> {len(lineage)} quarters, {sum(q.amendment_count for q in lineage)} amendments")
    for quarter in lineage:
        if quarter.amendment_count or quarter.needs_review:
            flag = "REVIEW" if quarter.needs_review else "ok"
            print(f"    {quarter.period_end} [{flag}] {quarter.reason}")

    print("\n==> parsing snapshots")
    parsed, flagged = 0, 0
    for ref in enriched:
        if ref.accession not in canonical:
            continue
        info = store.read_raw(ref.accession, INFO_TABLE_FILE)
        if info is None:
            continue
        managers = parse_manager_table(
            (store.read_raw(ref.accession, PRIMARY_DOC_FILE) or b"").decode("utf-8", "replace")
        )
        snapshot = parse_information_table(
            info.decode("utf-8", "replace"),
            entity=ENTITY,
            cik=CIK,
            accession=ref.accession,
            period_end=ref.period_end,
            filed_at=ref.filed_at,
            form_type=ref.form_type,
            manager_names=managers,
        )
        store.write_snapshot(snapshot)
        parsed += 1
        if snapshot.needs_review:
            flagged += 1
            print(f"    {ref.period_end} REVIEW: {snapshot.normalization.validation_detail}")

    version = DatasetVersion(
        name=args.version,
        entity=ENTITY,
        cik=CIK,
        requested_start=start,
        requested_end=end,
        actual_quarters=sorted(by_period),
    )
    store.write_version(version, lineage)

    print(f"\n==> {version.describe()}")
    print(f"    snapshots written: {parsed}   unit-validation flags: {flagged}")
    missing = version.missing_quarters
    if missing:
        print(f"    MISSING {len(missing)} quarter(s): {', '.join(str(m) for m in missing)}")
        print("    coverage above is what the dataset actually holds, not what was requested")
    return 0


def _fetch(client: EdgarClient, store: FilingStore, ref):
    """Ensure both documents are on disk, and read the amendment type from the cover page."""
    if not store.has_raw(ref.accession, PRIMARY_DOC_FILE):
        store.write_raw(
            ref.accession, PRIMARY_DOC_FILE, client.document(CIK, ref.accession, "primary_doc.xml")
        )
    if not store.has_raw(ref.accession, INFO_TABLE_FILE):
        name = client.information_table_name(CIK, ref.accession)
        if name is None:
            raise EdgarError("no information table document found in the filing directory")
        store.write_raw(ref.accession, INFO_TABLE_FILE, client.document(CIK, ref.accession, name))
        print(f"    fetched {ref.period_end} {ref.form_type} ({ref.accession})")

    primary = (store.read_raw(ref.accession, PRIMARY_DOC_FILE) or b"").decode("utf-8", "replace")
    return ref.model_copy(update={"amendment_type": amendment_type_from_primary_doc(primary)})


def _as_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


if __name__ == "__main__":
    raise SystemExit(main())
