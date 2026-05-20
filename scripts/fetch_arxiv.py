#!/usr/bin/env python3
"""Fetch latest arxiv cs.AI/cs.LG/cs.CL submissions (72h) -> data/arxiv.json.

Changes 2026-05-20:
  1) FIX boolean OR — query separator changed from "+" (literal plus, which
     `requests` url-encodes to %2B and arxiv treats as literal +) to a single
     space (which `requests` encodes to +, the actual arxiv OR separator).
     Old query silently returned 0 entries since day 1.
  2) sortBy=lastUpdatedDate so revisions surface (was submittedDate).
  3) window 48h -> 72h.
  4) explicit error string when items=[] so partial-failure status names a
     real reason instead of being silent.
"""
import json
import time
from datetime import datetime, timedelta, timezone
import requests
import xml.etree.ElementTree as ET

API = "http://export.arxiv.org/api/query"
QUERY = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL"
MAX_RESULTS = 120
WINDOW_HOURS = 72
CUTOFF = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
HEADERS = {"User-Agent": "tech-digest-mirror/1.1"}
TIMEOUT = 60
RETRY = 3


def fetch_arxiv():
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            r = requests.get(
                API,
                params={
                    "search_query": QUERY,
                    "sortBy": "lastUpdatedDate",
                    "sortOrder": "descending",
                    "max_results": MAX_RESULTS,
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            print(f"arxiv attempt {attempt}/{RETRY} failed: {type(e).__name__}: {str(e)[:120]}")
            if attempt < RETRY:
                time.sleep(15 * attempt)
    raise last_err


def write_empty(reason):
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "window_hours": WINDOW_HOURS,
        "items": [],
        "error": reason[:200],
    }
    with open("data/arxiv.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"arxiv.json: 0 items written with error={reason[:120]}")


try:
    xml_text = fetch_arxiv()
except Exception as e:
    write_empty(f"{type(e).__name__}: {str(e)}")
    raise SystemExit(0)

ATOM = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
ns = {"atom": ATOM, "arxiv": ARXIV_NS}

try:
    root = ET.fromstring(xml_text)
except ET.ParseError as e:
    write_empty(f"xml-parse-error: {str(e)[:120]} | snippet={xml_text[:120]!r}")
    raise SystemExit(0)

entries = root.findall("atom:entry", ns)
print(f"arxiv: API returned {len(entries)} entries (max_results={MAX_RESULTS})")

items = []
dropped_old = 0
for entry in entries:
    # Use whichever date is newest — submitted or last revision — so revisions
    # within the window still surface even if first submission was older.
    published = entry.findtext("atom:published", default="", namespaces=ns)
    updated = entry.findtext("atom:updated", default="", namespaces=ns)
    candidate_dt = None
    for date_str in (updated, published):
        if not date_str:
            continue
        try:
            candidate_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            break
        except Exception:
            continue
    if candidate_dt is None:
        continue
    if candidate_dt < CUTOFF:
        dropped_old += 1
        continue
    arxiv_id_full = entry.findtext("atom:id", default="", namespaces=ns)
    arxiv_id = arxiv_id_full.split("/abs/")[-1].split("v")[0] if "/abs/" in arxiv_id_full else arxiv_id_full
    title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip().replace("\n", " ")
    abstract = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip().replace("\n", " ")
    authors = [a.findtext("atom:name", default="", namespaces=ns) or "" for a in entry.findall("atom:author", ns)][:5]
    primary_cat_el = entry.find("arxiv:primary_category", ns)
    primary_category = primary_cat_el.get("term") if primary_cat_el is not None else ""
    items.append({
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract[:800],
        "primary_category": primary_category,
        "submitted_at": (published or updated),
        "updated_at": updated,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    })

if not items:
    write_empty(
        f"empty-after-filter: api_entries={len(entries)} dropped_old={dropped_old} "
        f"cutoff={CUTOFF.isoformat()}"
    )
    raise SystemExit(0)

out = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "cutoff": CUTOFF.isoformat(),
    "window_hours": WINDOW_HOURS,
    "items": items,
}
with open("data/arxiv.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"arxiv.json: {len(items)} items (last {WINDOW_HOURS}h, dropped_old={dropped_old})")
