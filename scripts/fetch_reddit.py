#!/usr/bin/env python3
"""Fetch Reddit hot posts from a fixed subreddit list, emit data/reddit.json.

Auth modes:
  - REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET present  → OAuth (script app), uses oauth.reddit.com.
    Required for GitHub Actions runners; Reddit blocks unauthenticated cloud-IP traffic.
  - Otherwise → unauthenticated www.reddit.com (works from residential IPs only).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SUBS: list[tuple[str, int, bool]] = [
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

CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "tech-digest-mirror/1.0 (by /u/scacola; +https://github.com/scacola/elon-tech-digest-mirror)",
)

WINDOW_SEC = 24 * 60 * 60


def tag_post(title: str, selftext: str) -> list[str]:
    text = (title + " " + selftext).lower()
    tags = [tag for tag, kws in INTEREST_TAGS.items() if any(k in text for k in kws)]
    return tags or ["general"]


def get_oauth_token() -> str | None:
    if not (CLIENT_ID and CLIENT_SECRET):
        return None
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
        token = payload.get("access_token")
        print(f"OAuth token acquired (expires in {payload.get('expires_in')}s)", file=sys.stderr)
        return token
    except Exception as e:
        print(f"OAuth token request failed: {e}", file=sys.stderr)
        return None


def fetch_sub(sub: str, limit: int, top_day: bool, token: str | None) -> list[dict]:
    if top_day:
        path = f"/r/{sub}/top.json?t=day&limit={limit}"
    else:
        path = f"/r/{sub}/hot.json?limit={limit}"

    if token:
        url = f"https://oauth.reddit.com{path}"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    else:
        url = f"https://www.reddit.com{path}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read())
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            print(f"  attempt {attempt}/3 fail: {e}", file=sys.stderr)
            if attempt == 3:
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
        blob = (title + " " + selftext).lower()
        if top_day and not any(k in blob for k in AI_FILTER_KW):
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
    token = get_oauth_token()
    if token:
        print(f"Auth mode: OAuth (oauth.reddit.com)", file=sys.stderr)
    else:
        print("Auth mode: none (www.reddit.com unauthenticated) — likely to be blocked from cloud IPs", file=sys.stderr)

    items: list[dict] = []
    failed: list[str] = []
    for sub, limit, top_day in SUBS:
        print(f"r/{sub} ({'top' if top_day else 'hot'}, limit={limit})", file=sys.stderr)
        res = fetch_sub(sub, limit, top_day, token)
        if not res:
            failed.append(sub)
        items.extend(res)
        time.sleep(1)

    out = {
        "source": "reddit",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "window": "24h",
        "auth_mode": "oauth" if token else "unauthenticated",
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
