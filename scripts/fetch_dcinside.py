#!/usr/bin/env python3
"""Fetch DCInside '특이점이 온다' minor gallery list + top bodies, emit data/dcinside.json.

Policy (2026-05-24 revision — user requested "fresh + humor heavy"):
  - Strict freshness window (default 72h, env DCINSIDE_WINDOW_HOURS). The legacy
    behavior of "exception_mode=recommend sorted by all-time recommend, top 15"
    surfaced 2024-10-31 / 2024-11-23 posts every single day because the concept
    list spans the full gallery history.
  - Scan BOTH the concept page (exception_mode=recommend) AND the general page
    (no exception_mode) so fresh humor / chatter posts are eligible — concept
    pages over-index on info posts.
  - Remove the recommend>=5 cutoff. The freshness window already filters cold
    posts; raising the floor was just hiding humor/short posts.
  - Pages: 5 (was 3). Cap: 50 (was 15). Body fetch: top 30 of those (was 15).
  - Tag `humor` on titles matching jpg/gif/mp4 attachments, ㅋㅋㅋ/ㅎㅎㅎ runs,
    "ㅅㅅ"/"ㅂㅈ"/"ㄹㅇ" reaction tokens, or known humor-leaning patterns so the
    downstream curator can route them into a 유머·밈 section.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

GALL_ID = "thesingularity"
LIST_URL = f"https://gall.dcinside.com/mgallery/board/lists/?id={GALL_ID}"
VIEW_URL = f"https://gall.dcinside.com/mgallery/board/view/?id={GALL_ID}&no="

WINDOW_HOURS = int(os.environ.get("DCINSIDE_WINDOW_HOURS", "72"))
MAX_PAGES = int(os.environ.get("DCINSIDE_MAX_PAGES", "5"))
MAX_ITEMS = int(os.environ.get("DCINSIDE_MAX_ITEMS", "50"))
BODY_TOP_N = int(os.environ.get("DCINSIDE_BODY_TOP_N", "30"))
KST = timezone(timedelta(hours=9))

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
HUMOR_PATTERNS = [
    re.compile(r"ㅋ{2,}"),
    re.compile(r"ㅎ{2,}"),
    re.compile(r"ㅅㅅ|ㅂㅈ|ㄹㅇ|ㅈㄴ|ㅆㅂ|ㅅㅂ|ㅈㄹ|ㅗㅗ"),
    re.compile(r"\.(jpg|jpeg|gif|png|webp|mp4|webm)\b", re.IGNORECASE),
    re.compile(r"(?i)(meme|짤|움짤|레전드|개꿀|개웃|병맛|개좆|좆같|존나|좆빠|개빡|개꿀잼|gpt 짤|클로드 짤|움짤)"),
    re.compile(r"(?i)\b(funny|lol|lmao|wtf|kek|copium|shitpost)\b"),
]

ROW_RE = re.compile(r'<tr\s+class="ub-content[^"]*"[^>]*>(?P<body>.*?)</tr>', re.DOTALL)
NUM_RE = re.compile(r'class="gall_num"[^>]*>\s*(\d+)\s*<')
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
    if any(p.search(text) for p in HUMOR_PATTERNS):
        tags.append("humor")
    return tags or ["general"]


def parse_dc_datetime(raw: str, now_kst: datetime) -> datetime | None:
    """Parse DC's relative date strings.

    Formats observed:
      "HH:MM"        — today (within last 24h, KST)
      "MM.DD"        — current year, no time
      "YY.MM.DD"     — older year (2-digit)
      "YYYY-MM-DD HH:MM:SS" — title tooltip absolute
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # absolute tooltip
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=KST)
        except ValueError:
            pass
    # HH:MM (today)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        cand = now_kst.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand > now_kst:
            cand -= timedelta(days=1)
        return cand
    # MM.DD (current year)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", raw)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        cand = now_kst.replace(month=mo, day=d, hour=12, minute=0, second=0, microsecond=0)
        if cand > now_kst + timedelta(days=1):
            cand = cand.replace(year=cand.year - 1)
        return cand
    # YY.MM.DD (old, 2-digit year — these are the 2024-10-31 culprits)
    m = re.fullmatch(r"(\d{2})\.(\d{1,2})\.(\d{1,2})", raw)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy
        return datetime(year, int(m.group(2)), int(m.group(3)), 12, 0, tzinfo=KST)
    return None


def parse_list(html: str) -> list[dict]:
    rows: list[dict] = []
    rows_seen = 0
    for m in ROW_RE.finditer(html):
        rows_seen += 1
        body = m.group("body")
        no_m = NUM_RE.search(body) or DATA_NO_RE.search(body)
        if not no_m:
            continue
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
    now_kst = datetime.now(KST)
    cutoff = now_kst - timedelta(hours=WINDOW_HOURS)
    print(f"Window: posts after {cutoff.isoformat()} (last {WINDOW_HOURS}h, KST)", file=sys.stderr)

    seen: set[str] = set()
    rows: list[dict] = []
    list_failed = 0
    dropped_old = 0
    dropped_unparseable = 0

    # Scan both concept-only and general listings so humor / fresh-but-low-recommend
    # posts get a fair shot. Concept page anchors info posts; general fills in chatter.
    list_modes = [
        ("concept", "&exception_mode=recommend"),
        ("general", ""),
    ]
    for mode_name, qs in list_modes:
        for page in range(1, MAX_PAGES + 1):
            url = f"{LIST_URL}{qs}&page={page}"
            print(f"list {mode_name} page {page}", file=sys.stderr)
            html = http_get(url)
            if not html:
                list_failed += 1
                continue
            for row in parse_list(html):
                if row["no"] in seen:
                    continue
                created = parse_dc_datetime(row["created_at_raw"], now_kst)
                if created is None:
                    dropped_unparseable += 1
                    continue
                if created < cutoff:
                    dropped_old += 1
                    continue
                seen.add(row["no"])
                row["created_at"] = created.isoformat()
                rows.append(row)
            time.sleep(1)

    # Rank: combine recency + popularity. recommend dominates within the window,
    # comments break ties, recency tiebreaks again so fresher wins among equals.
    def score(r: dict) -> tuple:
        c = datetime.fromisoformat(r["created_at"])
        return (r["recommend"], r["comments"], c)
    rows.sort(key=score, reverse=True)
    rows = rows[:MAX_ITEMS]

    print(
        f"Kept {len(rows)} (window {WINDOW_HOURS}h) | "
        f"dropped old={dropped_old} unparseable={dropped_unparseable}",
        file=sys.stderr,
    )

    for i, row in enumerate(rows):
        row["permalink"] = VIEW_URL + row["no"]
        if i < BODY_TOP_N:
            row["body_excerpt"] = fetch_body(row["no"])
            time.sleep(1)
        else:
            row["body_excerpt"] = ""
        row["tags"] = tag_post(row["title"] + " " + row["body_excerpt"])

    out = {
        "source": "dcinside",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "gallery": GALL_ID,
        "window": f"{WINDOW_HOURS}h",
        "items": rows,
        "stats": {
            "total": len(rows),
            "list_pages_failed": list_failed,
            "dropped_old": dropped_old,
            "dropped_unparseable": dropped_unparseable,
            "max_pages_scanned": MAX_PAGES,
        },
    }
    out_path = Path("data/dcinside.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(rows)} items", file=sys.stderr)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
