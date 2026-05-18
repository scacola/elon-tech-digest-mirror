#!/usr/bin/env python3
"""Fetch HN top stories, AI-keyword filter + 24h window → data/hackernews.json"""
import json
import time
from datetime import datetime, timedelta, timezone
import requests

AI_KEYWORDS = [
    "ai", "llm", "claude", "anthropic", "openai", "gpt", "chatgpt", "agent",
    "mcp", "embedding", "rag", "model", "transformer", "diffusion", "agentic",
    "mistral", "deepmind", "gemini", "llama", "huggingface", "fine-tun",
]
WINDOW_HOURS = 24
CUTOFF = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
HEADERS = {"User-Agent": "tech-digest-mirror/1.0"}

topstories = requests.get(
    "https://hacker-news.firebaseio.com/v0/topstories.json",
    headers=HEADERS, timeout=20,
).json()[:100]

items = []
for sid in topstories:
    try:
        item = requests.get(
            f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
            headers=HEADERS, timeout=10,
        ).json()
    except Exception:
        continue
    if not item or item.get("type") != "story":
        continue
    ts = item.get("time", 0)
    created = datetime.fromtimestamp(ts, tz=timezone.utc)
    if created < CUTOFF:
        continue
    title = item.get("title", "")
    if not any(kw in title.lower() for kw in AI_KEYWORDS):
        continue
    items.append({
        "hn_id": sid,
        "title": title,
        "score": item.get("score", 0),
        "by": item.get("by", ""),
        "comments": item.get("descendants", 0),
        "created_at": created.isoformat(),
        "url": item.get("url") or f"https://news.ycombinator.com/item?id={sid}",
        "discussion_url": f"https://news.ycombinator.com/item?id={sid}",
    })
    time.sleep(0.05)

items.sort(key=lambda x: x["score"], reverse=True)
items = items[:30]

out = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "cutoff": CUTOFF.isoformat(),
    "window_hours": WINDOW_HOURS,
    "items": items,
}
with open("data/hackernews.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"hackernews.json: {len(items)} items (AI-filtered, last {WINDOW_HOURS}h)")
