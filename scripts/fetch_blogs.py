#!/usr/bin/env python3
"""Fetch latest posts from major AI company blogs → data/blogs.json

Strategy:
1. RSS feeds (feedparser) for OpenAI / DeepMind / HuggingFace
2. Sitemap.xml + og:meta page fetch for Anthropic (RSS unavailable)
3. Meta AI / Mistral skipped for now — to be added in next iteration
"""
import json
import time
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

import feedparser
import requests
from bs4 import BeautifulSoup

RSS_SOURCES = [
    ("blog_openai",      "https://openai.com/news/rss.xml"),
    ("blog_deepmind",    "https://deepmind.google/blog/rss.xml"),
    ("blog_huggingface", "https://huggingface.co/blog/feed.xml"),
]

WINDOW_DAYS = 7
CUTOFF = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
HEADERS = {"User-Agent": "tech-digest-mirror/1.0 (+https://github.com/scacola/elon-tech-digest-mirror)"}
SM_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

items = []
attempted = []
seen_urls = set()


def add_record(source, url, status, count):
    attempted.append({"source": source, "url": url, "status": status, "count": count})


def fetch_rss(src_id, url):
    record_count = 0
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        if not feed.entries:
            add_record(src_id, url, f"empty (bozo={getattr(feed,'bozo',0)})", 0)
            return
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
            items.append({
                "source": src_id,
                "title": entry.get("title", "").strip(),
                "url": link,
                "published_at": published.isoformat(),
                "author": entry.get("author", ""),
                "lead_paragraph": (entry.get("summary", "") or "").strip()[:300],
            })
            record_count += 1
        add_record(src_id, url, "ok", record_count)
    except Exception as e:
        add_record(src_id, url, f"error: {type(e).__name__}: {str(e)[:80]}", record_count)


def fetch_anthropic_via_sitemap():
    sitemap_url = "https://www.anthropic.com/sitemap.xml"
    count = 0
    try:
        r = requests.get(sitemap_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        candidates = []
        for url_el in root.findall("sm:url", SM_NS):
            loc = url_el.findtext("sm:loc", default="", namespaces=SM_NS) or ""
            lastmod = url_el.findtext("sm:lastmod", default="", namespaces=SM_NS) or ""
            if "/news/" not in loc or loc.rstrip("/").endswith("/news"):
                continue
            try:
                lm_dt = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
            except Exception:
                continue
            if lm_dt < CUTOFF:
                continue
            candidates.append((lm_dt, loc))
        candidates.sort(reverse=True)
        # cap to 15 most recent — page fetches are expensive
        for lm_dt, loc in candidates[:15]:
            if loc in seen_urls:
                continue
            try:
                page = requests.get(loc, headers=HEADERS, timeout=15)
                page.raise_for_status()
                soup = BeautifulSoup(page.text, "html.parser")
                title_t = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
                desc_t = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
                title = (title_t.get("content", "").strip() if title_t else loc.rstrip("/").split("/")[-1].replace("-", " ").title())
                desc = (desc_t.get("content", "").strip() if desc_t else "")
            except Exception:
                continue
            seen_urls.add(loc)
            items.append({
                "source": "blog_anthropic",
                "title": title,
                "url": loc,
                "published_at": lm_dt.isoformat(),
                "author": "Anthropic",
                "lead_paragraph": desc[:300],
            })
            count += 1
            time.sleep(0.15)
        add_record("blog_anthropic", sitemap_url, "ok (sitemap+og)", count)
    except Exception as e:
        add_record("blog_anthropic", sitemap_url, f"error: {type(e).__name__}: {str(e)[:80]}", count)


# RSS feeds
for src_id, url in RSS_SOURCES:
    fetch_rss(src_id, url)

# Anthropic via sitemap+og
fetch_anthropic_via_sitemap()

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
