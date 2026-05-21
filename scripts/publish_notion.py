#!/usr/bin/env python3
"""Publish a digest notification to the Notion DB row + body.

Reads the notification JSON at $TARGET, creates a Notion page under
$DATA_SOURCE_ID with the digest_md content as blocks, then patches the
notification JSON in-place with the resulting page URL and pushes that
patch back so telegram-notify can include the link.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

VALID_SOURCES = {
    "reddit", "dcinside", "hackernews", "github", "arxiv",
    "blog_anthropic", "blog_openai", "blog_deepmind", "blog_google",
    "blog_meta", "blog_huggingface", "blog_mistral", "blog_cohere",
    "threads", "x",
}
VALID_STATUS = {"발행됨", "부분실패", "실패", "초안"}
VALID_TOP_TAG = {"claude", "agentic", "ai", "openai", "stocks", "general"}


def env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"missing env: {name}")
    return v


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------- markdown → Notion blocks ----------------------------------------

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def text_to_rich(text):
    if not text:
        return []
    pieces = []
    last = 0
    for m in MD_LINK_RE.finditer(text):
        if m.start() > last:
            pieces.append(("text", text[last:m.start()]))
        pieces.append(("link", m.group(1), m.group(2)))
        last = m.end()
    if last < len(text):
        pieces.append(("text", text[last:]))

    out = []
    for piece in pieces:
        if piece[0] == "link":
            label, url = piece[1], piece[2]
            out.append({
                "type": "text",
                "text": {"content": label[:1900], "link": {"url": url[:1900]}},
            })
            continue
        seg = piece[1]
        bold_pos = 0
        for bm in BOLD_RE.finditer(seg):
            if bm.start() > bold_pos:
                out.append({"type": "text", "text": {"content": seg[bold_pos:bm.start()][:1900]}})
            out.append({
                "type": "text",
                "text": {"content": bm.group(1)[:1900]},
                "annotations": {"bold": True},
            })
            bold_pos = bm.end()
        if bold_pos < len(seg):
            out.append({"type": "text", "text": {"content": seg[bold_pos:][:1900]}})
    return out


def md_to_blocks(md):
    blocks = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue
        if line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": text_to_rich(line[4:].strip())}})
            i += 1
            continue
        if line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": text_to_rich(line[3:].strip())}})
            i += 1
            continue
        if line.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": text_to_rich(line[2:].strip())}})
            i += 1
            continue
        if line.startswith("> "):
            quote_lines = []
            while i < len(lines) and (lines[i].startswith("> ") or lines[i].strip() == ">"):
                quote_lines.append(lines[i][2:] if lines[i].startswith("> ") else "")
                i += 1
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": text_to_rich("\n".join(quote_lines).strip())}})
            continue
        if line.startswith("- ") or line.startswith("* "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": text_to_rich(line[2:].strip())}})
            i += 1
            continue
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": text_to_rich(line)}})
        i += 1
    return blocks


# ---------- Notion API ------------------------------------------------------

def build_properties(notify):
    date = notify.get("date") or ""
    headline = (notify.get("headline") or "")[:1900]

    status = notify.get("status")
    if status not in VALID_STATUS:
        sys.exit(f"invalid status: {status!r} (must be one of {VALID_STATUS})")

    top_tag = notify.get("top_tag") or "general"
    if top_tag not in VALID_TOP_TAG:
        top_tag = "general"

    raw_sources = notify.get("sources") or []
    if isinstance(raw_sources, str):
        raw_sources = json.loads(raw_sources) if raw_sources.startswith("[") else \
                      [s.strip() for s in raw_sources.split(",") if s.strip()]
    sources = [s for s in raw_sources if s in VALID_SOURCES]

    props = {
        "제목": {"title": [{"type": "text", "text": {"content": f"🧠 테크 다이제스트 {date}"}}]},
        "상태": {"select": {"name": status}},
        "항목수": {"number": int(notify.get("n_items") or 0)},
        "클러스터수": {"number": int(notify.get("n_clusters") or 0)},
        "상위태그": {"select": {"name": top_tag}},
        "소스": {"multi_select": [{"name": s} for s in sources]},
        "Reddit수": {"number": int(notify.get("n_reddit") or 0)},
        "DCInside수": {"number": int(notify.get("n_dcinside") or 0)},
        "HN수": {"number": int(notify.get("n_hackernews") or 0)},
    }
    if date:
        props["날짜"] = {"date": {"start": date}}
    if headline:
        props["헤드라인"] = {"rich_text": text_to_rich(headline)}
    if notify.get("first_url"):
        props["원문링크"] = {"url": notify["first_url"]}
    return props


def chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def create_page(token, data_source_id, properties, digest_md):
    blocks = md_to_blocks(digest_md)
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": data_source_id},
        "icon": {"type": "emoji", "emoji": "🧠"},
        "properties": properties,
        "children": blocks[:100],
    }
    r = requests.post(f"{NOTION_API}/pages", headers=headers(token), json=payload, timeout=60)
    if r.status_code >= 400:
        print(f"create page failed: {r.status_code}\n{r.text[:600]}", file=sys.stderr)
        r.raise_for_status()
    page = r.json()
    page_id = page["id"]
    page_url = page["url"]

    for batch in chunk(blocks[100:], 100):
        ar = requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=headers(token), json={"children": batch}, timeout=60,
        )
        ar.raise_for_status()
    return page_id, page_url


def patch_notification(target, page_url):
    p = Path(target)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["notion_url"] = page_url
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_push_patch(target, page_url):
    subprocess.run(["git", "config", "user.name", "notion-publish-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "bot@noreply.github.com"], check=True)
    subprocess.run(["git", "add", target], check=True)
    if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        print("no changes to commit")
        return
    msg = f"notion-publish: {Path(target).name} -> {page_url}"
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "pull", "--rebase"], check=True)
    subprocess.run(["git", "push"], check=True)


def main():
    token = env("NOTION_TOKEN")
    data_source_id = env("DATA_SOURCE_ID")
    target = env("TARGET")

    notify = json.loads(Path(target).read_text(encoding="utf-8"))
    print(f"Publishing: {target}")
    print(f"  status={notify.get('status')} date={notify.get('date')} n_items={notify.get('n_items')}")

    if notify.get("notion_url"):
        print(f"already published: {notify['notion_url']}")
        return

    digest_md = notify.get("digest_md")
    if not digest_md:
        sys.exit("notification missing digest_md — routine must include the full markdown")

    page_id, page_url = create_page(token, data_source_id, build_properties(notify), digest_md)
    print(f"created: {page_url}")
    patch_notification(target, page_url)
    git_push_patch(target, page_url)


if __name__ == "__main__":
    main()
