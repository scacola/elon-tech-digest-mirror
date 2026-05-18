#!/usr/bin/env python3
"""Fetch latest arxiv cs.AI/cs.LG/cs.CL submissions (48h) → data/arxiv.json"""
import json
import time
from datetime import datetime, timedelta, timezone
import requests
import xml.etree.ElementTree as ET

API = "http://export.arxiv.org/api/query"
QUERY = "cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
MAX_RESULTS = 80
WINDOW_HOURS = 48
CUTOFF = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
HEADERS = {"User-Agent": "tech-digest-mirror/1.0"}
TIMEOUT = 60  # arxiv API can be slow
RETRY = 2


def fetch_arxiv():
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            r = requests.get(
                API,
                params={
                    "search_query": QUERY,
                    "sortBy": "submittedDate",
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
                time.sleep(15)
    raise last_err


try:
    xml_text = fetch_arxiv()
except Exception as e:
    # graceful empty output so workflow doesn't fail and mirror still publishes other sources
    print(f"arxiv: giving up after {RETRY} attempts — writing empty payload")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "window_hours": WINDOW_HOURS,
        "items": [],
        "error": f"{type(e).__name__}: {str(e)[:120]}",
    }
    with open("data/arxiv.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    raise SystemExit(0)

ATOM = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
ns = {"atom": ATOM, "arxiv": ARXIV_NS}

root = ET.fromstring(xml_text)
items = []
for entry in root.findall("atom:entry", ns):
    published = entry.findtext("atom:published", default="", namespaces=ns)
    try:
        submitted_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except Exception:
        continue
    if submitted_dt < CUTOFF:
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
        "submitted_at": submitted_dt.isoformat(),
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    })

out = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "cutoff": CUTOFF.isoformat(),
    "window_hours": WINDOW_HOURS,
    "items": items,
}
with open("data/arxiv.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"arxiv.json: {len(items)} items (last {WINDOW_HOURS}h)")
