#!/usr/bin/env python3
"""Fetch latest posts from major AI company blogs (RSS) → data/blogs.json"""
import json
from datetime import datetime, timedelta, timezone
import feedparser

SOURCES = [
    ("blog_anthropic",   "https://www.anthropic.com/rss/news.xml"),
    ("blog_anthropic2",  "https://www.anthropic.com/news.xml"),
    ("blog_openai",      "https://openai.com/news/rss.xml"),
    ("blog_deepmind",    "https://deepmind.google/blog/rss.xml"),
    ("blog_meta",        "https://ai.meta.com/blog/rss/"),
    ("blog_huggingface", "https://huggingface.co/blog/feed.xml"),
    ("blog_mistral",     "https://mistral.ai/news/rss.xml"),
]

WINDOW_DAYS = 7
CUTOFF = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
HEADERS = {"User-Agent": "tech-digest-mirror/1.0 (+https://github.com/scacola/elon-tech-digest-mirror)"}

items = []
attempted = []
seen_urls = set()

for src_id, url in SOURCES:
    record = {"source": src_id, "url": url, "status": "unknown", "count": 0}
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        if not feed.entries:
            record["status"] = f"empty (bozo={getattr(feed,'bozo',0)})"
            attempted.append(record)
            continue
        for entry in feed.entries:
            published = None
            for k in ("published_parsed", "updated_parsed"):
                t = entry.get(k)
                if t:
                    published = datetime(*t[:6], tzinfo=timezone.utc)
                    break
            if not published or published < CUTOFF:
                continue
            link = entry.get("link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            # canonicalize anthropic2 alias to anthropic
            label = "blog_anthropic" if src_id == "blog_anthropic2" else src_id
            items.append({
                "source": label,
                "title": entry.get("title", "").strip(),
                "url": link,
                "published_at": published.isoformat(),
                "author": entry.get("author", ""),
                "lead_paragraph": (entry.get("summary", "") or "").strip()[:300],
            })
            record["count"] += 1
        record["status"] = "ok"
    except Exception as e:
        record["status"] = f"error: {type(e).__name__}: {str(e)[:80]}"
    attempted.append(record)

out = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "cutoff": CUTOFF.isoformat(),
    "window_days": WINDOW_DAYS,
    "attempted": attempted,
    "items": items,
}
with open("data/blogs.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"blogs.json: {len(items)} items total")
for r in attempted:
    print(f"  {r['source']}: {r['status']} ({r['count']} new)")
