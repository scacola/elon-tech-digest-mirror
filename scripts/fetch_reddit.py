#!/usr/bin/env python3
"""Fetch Reddit posts via Arctic Shift (Pushshift successor), emit data/reddit.json.

Policy (2026-05-24 revision — user requested "fresh + humor heavy"):
  - Per-sub limits raised so curator has a fatter pool to choose from.
  - Meme/Humor/Shitpost/Comedy/Satire flairs are NO LONGER blocked. They now
    carry a `humor` interest-tag so the curator can route them into a 유머·밈
    section instead of dropping them on the floor.
  - 24h freshness window unchanged — Arctic Shift's max age is also why we keep it.
  - Stocks/wallstreetbets AI keyword filter unchanged (user explicitly only wants
    AI/tech tickers, not e.g. retail/airline plays).

Why Arctic Shift instead of reddit.com/.json:
  - reddit.com unauthenticated → 403 from GitHub Actions IPs.
  - Reddit OAuth app registration has been gated behind the new Devvit/Responsible
    Builder flow; the classic /prefs/apps script-app path is no longer reliable.
  - Arctic Shift mirrors Reddit posts shortly after they're posted and exposes them
    over a no-auth HTTP API.

Trade-off:
  Score and num_comments are captured at insertion time (~(1, 0..2)). Downstream
  curator weights items by interest-tag keywords in title/body anyway.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# (subreddit, limit, ai_filter)
SUBS: list[tuple[str, int, bool]] = [
    ("singularity", 50, False),
    ("ClaudeAI", 50, False),
    ("ClaudeCode", 50, False),
    ("LocalLLaMA", 40, False),
    ("OpenAI", 35, False),
    ("AI_Agents", 35, False),
    ("MachineLearning", 30, False),
    ("LLMDevs", 30, False),
    ("ChatGPT", 30, False),
    ("wallstreetbets", 60, True),
    ("stocks", 40, True),
]

INTEREST_TAGS = {
    "claude": ["claude", "anthropic", "openclaw", "openclaude"],
    "agentic": ["agent", "agentic", "mcp", "autonomous", "swarm"],
    "ai": ["llm", "gpt", "ai ", " ai", "model", "rag", "embedding"],
    "openai": ["openai", "chatgpt", "codex", "sora", "gpt-"],
    "stocks": ["nvda", "aapl", "msft", "googl", "amd", "tsla", "stock", "earnings"],
}
HUMOR_FLAIRS = {"meme", "memes", "humor", "humour", "shitpost", "comedy", "satire", "funny", "joke"}
AI_FILTER_KW = [
    "ai", "llm", "gpt", "claude", "anthropic", "openai", "agent", "mcp",
    "nvda", "tsla", "model", "transformer",
]

ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"
USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "tech-digest-mirror/1.0 (by /u/scacola; +https://github.com/scacola/elon-tech-digest-mirror)",
)
WINDOW_SEC = int(os.environ.get("REDDIT_WINDOW_HOURS", "24")) * 60 * 60


def tag_post(title: str, selftext: str, flair: str) -> list[str]:
    text = (title + " " + selftext).lower()
    tags = [tag for tag, kws in INTEREST_TAGS.items() if any(k in text for k in kws)]
    flair_l = (flair or "").strip().lower()
    if flair_l in HUMOR_FLAIRS:
        tags.append("humor")
    return tags or ["general"]


def fetch_sub(sub: str, limit: int, ai_filter: bool) -> list[dict]:
    params = urllib.parse.urlencode({"subreddit": sub, "limit": limit})
    url = f"{ARCTIC_SHIFT_BASE}?{params}"
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as r:
                payload = json.loads(r.read())
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            print(f"  attempt {attempt}/3 fail: {e}", file=sys.stderr)
            if attempt == 3:
                return []
            time.sleep(2 * attempt)

    raw = payload.get("data") or []
    now = time.time()
    out: list[dict] = []
    for d in raw:
        if d.get("over_18"):
            continue
        # NOTE: humor / meme / shitpost flairs are deliberately ALLOWED (user wants
        # humor in the digest). They get tagged via tag_post() so the curator can
        # route them. The only flair we still suppress is none — keep all.
        created = float(d.get("created_utc") or 0)
        if not created or now - created > WINDOW_SEC:
            continue
        title = d.get("title") or ""
        selftext = (d.get("selftext") or "")[:200]
        blob = (title + " " + selftext).lower()
        if ai_filter and not any(k in blob for k in AI_FILTER_KW):
            continue
        post_id = d.get("id") or ""
        permalink = d.get("permalink") or f"/r/{sub}/comments/{post_id}/"
        flair = d.get("link_flair_text") or ""
        out.append({
            "id": post_id,
            "subreddit": sub,
            "title": title,
            # score/num_comments from Arctic Shift are snapshots at insertion;
            # treat as soft signal only — curator weights by content keywords.
            "score": int(d.get("score") or 0),
            "num_comments": int(d.get("num_comments") or 0),
            "created_utc": created,
            "permalink": f"https://reddit.com{permalink}" if permalink.startswith("/") else permalink,
            "url": d.get("url") or "",
            "selftext_excerpt": selftext,
            "author": d.get("author"),
            "flair": flair,
            "tags": tag_post(title, selftext, flair),
        })
    return out


def main() -> int:
    print(f"Source: Arctic Shift ({ARCTIC_SHIFT_BASE})", file=sys.stderr)
    print(f"Window: {WINDOW_SEC // 3600}h", file=sys.stderr)
    items: list[dict] = []
    failed: list[str] = []
    for sub, limit, ai_filter in SUBS:
        print(f"r/{sub} (limit={limit}, ai_filter={ai_filter})", file=sys.stderr)
        res = fetch_sub(sub, limit, ai_filter)
        if not res:
            failed.append(sub)
        items.extend(res)
        time.sleep(1)

    out = {
        "source": "reddit",
        "via": "arctic_shift",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "window": f"{WINDOW_SEC // 3600}h",
        "items": items,
        "stats": {
            "total": len(items),
            "subreddits_attempted": len(SUBS),
            "subreddits_failed": failed,
            "humor_tagged": sum(1 for it in items if "humor" in it.get("tags", [])),
        },
        "notes": (
            "score and num_comments reflect Arctic Shift insertion-time snapshots, "
            "not live values; downstream curator should weight by content keywords. "
            "Humor/meme flairs are allowed and tagged `humor`."
        ),
    }
    out_path = Path("data/reddit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(items)} items, {len(failed)} failed subs", file=sys.stderr)
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
