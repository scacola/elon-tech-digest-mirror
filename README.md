# elon-tech-digest-mirror

Reddit + DCInside data mirror for the [elon tech-digest harness](https://github.com/scacola/elon).

## Why this exists

The daily tech-digest runs as a cron on Anthropic's cloud routine. That environment's outbound network policy blocks `reddit.com`, `gall.dcinside.com`, and most general public proxies (Jina Reader, AllOrigins). GitHub is reachable, so this repo acts as a proxy: a GitHub Actions workflow fetches both sources every morning and commits a fresh JSON, which the routine then reads via `raw.githubusercontent.com`.

## Schedule

- GitHub Actions cron: `30 22 * * *` (22:30 UTC daily = **07:30 KST**, 30 min before the routine)
- Consumer cron (claude.ai routine `trig_017LKz5F2zVCeo6CihkzzWNY`): `0 23 * * *` (23:00 UTC = **08:00 KST**)

## Data

- `data/reddit.json` — 10 subreddits, 24 h window, NSFW/Meme filtered. Schema matches the `reddit-scout` agent.
- `data/dcinside.json` — 특이점이 온다 갤러리, 3 list pages, top 15 bodies, recommend ≥ 5.

### Raw URLs (consumed by the routine)

```
https://raw.githubusercontent.com/scacola/elon-tech-digest-mirror/main/data/reddit.json
https://raw.githubusercontent.com/scacola/elon-tech-digest-mirror/main/data/dcinside.json
```

## Manual run

```bash
gh workflow run fetch.yml --repo scacola/elon-tech-digest-mirror
gh run list --repo scacola/elon-tech-digest-mirror --workflow fetch.yml --limit 1
```

## Local test

```bash
python scripts/fetch_reddit.py    # → data/reddit.json
python scripts/fetch_dcinside.py  # → data/dcinside.json
```
