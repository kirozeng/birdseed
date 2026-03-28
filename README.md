# 🐦 birdseed

Sync your X (Twitter) bookmarks to Obsidian. Locally, incrementally, with AI.

```
bookmarks → GraphQL API → AI enrichment → Obsidian Markdown
```

## Why birdseed?

- **No browser automation** — Uses X's internal GraphQL API, not Playwright/Selenium scraping
- **X Articles supported** — Full plain text for long-form posts (not just a t.co link)
- **AI enrichment** — Auto-summary, topic tags, English→Chinese translation (Gemini or Claude)
- **Incremental** — Only syncs new bookmarks, tracks what's been processed
- **Local & private** — Your data stays on your machine, no third-party services
- **Zero config to start** — Just log in and run

## Quick Start

### 1. Install

```bash
git clone https://github.com/kirozeng/birdseed.git
cd birdseed

# Required for login only
pip install playwright
python -m playwright install chromium

# Optional: external article extraction
pip install trafilatura lxml_html_clean
```

### 2. Log in to X

```bash
python3 login.py
```

This opens Chrome. Log in to X manually (Google SSO, MFA all supported).
Press Enter after your bookmarks page loads. Done — cookies saved locally.

### 3. Sync

```bash
# First sync — fetches up to 200 bookmarks
python3 sync.py

# With verbose logging
python3 sync.py -v

# Custom output directory
python3 sync.py --output-dir ~/my-obsidian-vault/X-Bookmarks

# Limit how many to fetch
python3 sync.py --limit 50
```

That's it. Open the output folder in Obsidian.

## What You Get

Each bookmark becomes a Markdown note:

```
2026-03-25-How-To-Make-Obsidian-Beautiful.md
2026-03-26-Claude-Obsidian最猛方案-Filesystem-MCP教程.md
```

With rich frontmatter:

```yaml
---
source: x-bookmarks
author: "@jameesy"
author_name: "James Bedford"
tweet_id: "2036795753096421843"
tweet_url: "https://x.com/jameesy/status/2036795753096421843"
date: 2026-03-25
title: "How To Make Obsidian Beautiful"
likes: 1517
retweets: 97
replies: 24
tags: [x-bookmark, 效率工具, 教程, 开源]
---

> **摘要：** AI-generated summary here...

Full article content...

---

**中文翻译：** (auto-translated for English content)

---

❤️ 1517 🔁 97 💬 24
```

Plus:
- 📸 Local images in `media/` folder
- 📑 Auto-generated `X Bookmarks Index.md` (MOC grouped by month)
- 🎥 Video URLs extracted
- 💬 Quote tweets rendered

## AI Enrichment (Optional)

Set one of these environment variables to enable AI features:

```bash
# Gemini (recommended, has free tier)
export GEMINI_API_KEY="your-key"

# Or Claude
export ANTHROPIC_API_KEY="your-key"
```

| Feature | Without AI | With AI |
|---------|-----------|---------|
| Summary | ❌ | ✅ 3-5 sentence Chinese summary |
| Tags | `[x-bookmark]` only | ✅ Auto-classified (AI, Design, etc.) |
| Translation | ❌ | ✅ English → Chinese |

birdseed works perfectly fine without AI — you just won't get summaries, smart tags, or translations.

## All Options

```
python3 sync.py --help

Options:
  --output-dir PATH     Output directory (default: ~/birdseed-output)
  --limit N             Max bookmarks to fetch (default: 200)
  --fetch-articles      Extract external article text (default: on)
  --no-fetch-articles   Skip external article extraction
  --download-media      Download images locally (default: on)
  --no-download-media   Skip image download
  --rewrite-visible     Rewrite notes even if already synced
  -v, --verbose         Debug logging
  -q, --quiet           Warnings only
```

## How It Works

```
1. Load cookies from data/storage_state.json
2. Fetch bookmarks via X GraphQL API (/i/api/graphql/.../Bookmarks)
3. For X Articles → fetch full text via TweetDetail API
4. Resolve t.co short URLs
5. Extract external article text (trafilatura, optional)
6. AI: generate summary + tags in one call
7. AI: translate English content to Chinese
8. Write Markdown notes + download images
9. Generate MOC index
10. Update state.json (incremental tracking)
```

## Scheduling

### cron (Linux/macOS)

```bash
# Every day at 10am
0 10 * * * cd /path/to/birdseed && python3 sync.py -q
```

### Obsidian + OpenClaw

If you use [OpenClaw](https://github.com/openclaw/openclaw), birdseed can run as a heartbeat task.

## Configuration

### Output directory

Option 1: CLI flag
```bash
python3 sync.py --output-dir /path/to/obsidian/vault/X-Bookmarks
```

Option 2: Config file (`data/config.json`)
```json
{
  "output_dir": "/path/to/obsidian/vault/X-Bookmarks"
}
```

### Cookie refresh

Cookies typically last weeks to months. When they expire, sync.py exits with code 2:

```
Authentication failed (401). Cookie may be expired.
```

Just re-run `python3 login.py`.

## FAQ

**Q: Is this against X's ToS?**
A: This tool uses X's internal web API with your own login cookies — the same requests your browser makes. It only reads your own bookmarks and performs no write actions. Use responsibly.

**Q: What are X Articles?**
A: X's long-form post feature (no character limit). birdseed fetches the full plain text via a separate API call.

**Q: Can I use this without Obsidian?**
A: Yes. The output is standard Markdown files. Works with any Markdown editor or note-taking app.

**Q: Do I need an AI API key?**
A: No. AI features (summary, tags, translation) are optional enhancements.

## Comparison

| | birdseed | Readwise | x2o |
|---|---|---|---|
| Price | Free | ~$8/month | Free |
| Privacy | Fully local | Cloud service | Fully local |
| X Articles | ✅ Full text | ❌ | ❌ |
| AI enrichment | ✅ Summary + tags + translation | Basic | ✅ Classification |
| Incremental sync | ✅ | ✅ | ❌ (full export) |
| External articles | ✅ (trafilatura) | ✅ | ❌ |
| Local images | ✅ | ❌ | ❌ |
| Engagement metrics | ✅ | ✅ | ✅ |
| Multi-platform reading | ❌ | ✅ (web, mobile, Kindle) | ❌ |
| Beyond X bookmarks | ❌ | ✅ (Kindle, RSS, PDF, etc.) | ❌ |

## 中文说明

birdseed 是一个将 X（Twitter）收藏夹同步到 Obsidian 的本地工具。

**特点：**
- 使用 X 内部 GraphQL API，不依赖浏览器自动化
- 支持 X Article 长文（完整提取纯文本和标题）
- AI 自动生成摘要、分类标签、英译中翻译（Gemini / Claude）
- 增量同步，只处理新收藏
- 图片本地下载
- 数据完全本地，不上传任何第三方服务

**快速开始：**
```bash
git clone https://github.com/kirozeng/birdseed.git
cd birdseed
pip install playwright && python -m playwright install chromium
python3 login.py     # 打开浏览器登录 X
python3 sync.py      # 同步收藏夹
```

详细说明见上方英文文档。

---

## License

MIT

## Credits

Inspired by [x2o](https://github.com/kiki123124/x2o) for the GraphQL API approach.

Built with 🐦 and 🤖 by Kiro Zeng.
