#!/usr/bin/env python3
"""Fetch DCInside '특이점이 온다' minor gallery list + top bodies, emit data/dcinside.json."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

GALL_ID = "thesingularity"
LIST_URL = f"https://gall.dcinside.com/mgallery/board/lists/?id={GALL_ID}"
VIEW_URL = f"https://gall.dcinside.com/mgallery/board/view/?id={GALL_ID}&no="

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

INTEREST_TAGS = {
    "claude": ["claude", "anthropic", "openclaw", "openclaude", "오픈클로", "클로드", "앤트로픽"],
    "agentic": ["에이전트", "agent", "mcp", "agentic", "오토노머스"],
    "ai": ["ai", "llm", "gpt", "ml", "ai모델", "모델", "인공지능"],
    "openai": ["openai", "chatgpt", "오픈ai", "codex", "sora", "샘 알트만"],
    "stocks": ["nvda", "주식", "엔비디아", "stock", "테슬라", "tsla"],
}

ROW_RE = re.compile(r'<tr\s+class="ub-content[^"]*"[^>]*>(?P<body>.*?)</tr>', re.DOTALL)
DATA_NO_RE = re.compile(r'\bdata-no="(\d+)"')
TITLE_RE = re.compile(
    r'<a[^>]+href="/mgallery/board/view/\?id=' + GALL_ID + r'&(?:amp;)?no=\d+[^"]*"[^>]*>(?P<t>.*?)</a>',
    re.DOTALL,
)
RECOM_RE = re.compile(r'class="gall_recommend"[^>]*>\s*(\d+)')
COMMENT_RE = re.compile(r'class="reply_num"[^>]*>\[?(\d+)')
AUTHOR_RE = re.compile(r'class="gall_writer[^"]*"[^>]*\bdata-nick="([^"]*)"')
DATE_RE = re.compile(r'class="gall_date"[^>]*(?:title="([^"]+)")?[^>]*>([^<]+)<')
BODY_RE = re.compile(r'<div[^>]+class="write_div"[^>]*>(?P<b>.*?)</div>\s*(?:<div[^>]+class="op_box|</section)', re.DOTALL)


def http_get(url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            print(f"  attempt {attempt}/{retries} fail: {e} for {url[:80]}", file=sys.stderr)
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def strip_tags(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html)).strip()


def tag_post(text: str) -> list[str]:
    t = text.lower()
    tags = [tag for tag, kws in INTEREST_TAGS.items() if any(kw in t for kw in kws)]
    return tags or ["general"]


def parse_list(html: str) -> list[dict]:
    rows: list[dict] = []
    rows_seen = 0
    debug_first_logged = False
    for m in ROW_RE.finditer(html):
        rows_seen += 1
        body = m.group("body")
        if rows_seen <= 2 and not debug_first_logged:
            preview = re.sub(r"\s+", " ", body[:400])
            print(f"  DEBUG row#{rows_seen} preview: {preview}", file=sys.stderr)
            debug_first_logged = (rows_seen == 2)
        no_m = DATA_NO_RE.search(body)
        if not no_m:
            continue  # notice / survey row
        title_m = TITLE_RE.search(body)
        if not title_m:
            continue
        title = strip_tags(title_m.group("t"))
        if not title:
            continue
        recom_m = RECOM_RE.search(body)
        comm_m = COMMENT_RE.search(body)
        author_m = AUTHOR_RE.search(body)
        date_m = DATE_RE.search(body)
        rows.append({
            "no": no_m.group(1),
            "title": title,
            "recommend": int(recom_m.group(1)) if recom_m else 0,
            "comments": int(comm_m.group(1)) if comm_m else 0,
            "author": author_m.group(1) if author_m else "",
            "created_at_raw": (date_m.group(1) or date_m.group(2) if date_m else "").strip(),
        })
    print(f"  parsed {len(rows)} posts (from {rows_seen} rows)", file=sys.stderr)
    return rows


def fetch_body(no: str) -> str:
    html = http_get(VIEW_URL + no)
    if not html:
        return ""
    m = BODY_RE.search(html)
    if not m:
        return ""
    return strip_tags(m.group("b"))[:300]


def main() -> int:
    seen: set[str] = set()
    rows: list[dict] = []
    list_failed = 0
    for page in range(1, 4):
        url = f"{LIST_URL}&exception_mode=recommend&page={page}"
        print(f"list page {page}", file=sys.stderr)
        html = http_get(url)
        if not html:
            list_failed += 1
            continue
        print(f"  html size: {len(html)} bytes", file=sys.stderr)
        for row in parse_list(html):
            if row["no"] in seen:
                continue
            seen.add(row["no"])
            if row["recommend"] < 5:
                continue
            rows.append(row)
        time.sleep(1)

    rows.sort(key=lambda x: x["recommend"], reverse=True)
    rows = rows[:15]

    for row in rows:
        row["body_excerpt"] = fetch_body(row["no"])
        row["permalink"] = VIEW_URL + row["no"]
        row["tags"] = tag_post(row["title"] + " " + row["body_excerpt"])
        time.sleep(1)

    out = {
        "source": "dcinside",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "gallery": GALL_ID,
        "window": "24h",
        "items": rows,
        "stats": {"total": len(rows), "list_pages_failed": list_failed},
    }
    out_path = Path("data/dcinside.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(rows)} items", file=sys.stderr)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
