#!/usr/bin/env python3
"""Fetch Reddit hot posts from a fixed subreddit list, filter 24h, emit data/reddit.json.

Schema matches the reddit-scout agent so the curator can consume the file as-is.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SUBS: list[tuple[str, int, bool]] = [
    # (subreddit, limit, top_day?)
    ("singularity", 25, False),
    ("ClaudeAI", 25, False),
    ("ClaudeCode", 25, False),
    ("LocalLLaMA", 20, False),
    ("OpenAI", 15, False),
    ("AI_Agents", 20, False),
    ("MachineLearning", 15, False),
    ("LLMDevs", 15, False),
    ("wallstreetbets", 30, True),
    ("stocks", 20, True),
]

INTEREST_TAGS = {
    "claude": ["claude", "anthropic", "openclaw", "openclaude"],
    "agentic": ["agent", "agentic", "mcp", "autonomous", "swarm"],
    "ai": ["llm", "gpt", "ai ", " ai", "model", "rag", "embedding"],
    "openai": ["openai", "chatgpt", "codex", "sora", "gpt-"],
    "stocks": ["nvda", "aapl", "msft", "googl", "amd", "tsla", "stock", "earnings"],
}
AI_FILTER_KW = [
    "ai", "llm", "gpt", "claude", "anthropic", "openai", "agent", "mcp",
    "nvda", "tsla", "model", "transformer",
]

USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "tech-digest-mirror/1.0 (by /u/scacola; +https://github.com/scacola/elon-tech-digest-mirror)",
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
WINDOW_SEC = 24 * 60 * 60


def tag_post(title: str, selftext: str) -> list[str]:
    text = (title + " " + selftext).lower()
    tags = [tag for tag, kws in INTEREST_TAGS.items() if any(k in text for k in kws)]
    return tags or ["general"]


def fetch_sub(sub: str, limit: int, top_day: bool, max_retries: int = 3) -> list[dict]:
    if top_day:
        url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}"
    else:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read())
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            print(f"  attempt {attempt}/{max_retries} fail: {e}", file=sys.stderr)
            if attempt == max_retries:
                return []
            time.sleep(2 * attempt)

    now = time.time()
    out: list[dict] = []
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("over_18"):
            continue
        if (d.get("link_flair_text") or "").lower() == "meme":
            continue
        if now - float(d.get("created_utc") or 0) > WINDOW_SEC:
            continue
        title = d.get("title") or ""
        selftext = (d.get("selftext") or "")[:200]
        body_blob = (title + " " + selftext).lower()
        if top_day and not any(k in body_blob for k in AI_FILTER_KW):
            continue
        out.append({
            "id": d.get("id"),
            "subreddit": sub,
            "title": title,
            "score": int(d.get("score") or 0),
            "num_comments": int(d.get("num_comments") or 0),
            "created_utc": d.get("created_utc"),
            "permalink": f"https://reddit.com{d.get('permalink', '')}",
            "url": d.get("url") or "",
            "selftext_excerpt": selftext,
            "author": d.get("author"),
            "tags": tag_post(title, selftext),
        })
    return out


def main() -> int:
    items: list[dict] = []
    failed: list[str] = []
    for sub, limit, top_day in SUBS:
        print(f"r/{sub} ({'top' if top_day else 'hot'}, limit={limit})", file=sys.stderr)
        res = fetch_sub(sub, limit, top_day)
        if not res:
            failed.append(sub)
        items.extend(res)
        time.sleep(2)

    out = {
        "source": "reddit",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "window": "24h",
        "items": items,
        "stats": {
            "total": len(items),
            "subreddits_attempted": len(SUBS),
            "subreddits_failed": failed,
        },
    }
    out_path = Path("data/reddit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(items)} items, {len(failed)} failed subs", file=sys.stderr)
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
