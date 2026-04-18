# 🐦 birdseed

[![Tests](https://github.com/kirozeng/birdseed/actions/workflows/test.yml/badge.svg)](https://github.com/kirozeng/birdseed/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

Sync your X (Twitter) bookmarks to Obsidian. Locally, incrementally, with AI.

```
bookmarks → GraphQL API → AI enrichment → Obsidian Markdown
```

## Why birdseed?

- **No browser automation** — Uses X's internal GraphQL API, not Playwright/Selenium scraping
- **X Articles supported** — Full plain text for long-form posts (not just a t.co link)
- **AI enrichment** — Auto-summary, topic tags, and optional translation (Gemini or Claude)
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

### 2. Authenticate with X

**Option A: Browser login (recommended for first time)**
```bash
python3 login.py
```
Opens Chrome. Log in to X manually (Google SSO, MFA all supported).
Press Enter after your bookmarks page loads. Done — cookies saved locally.

**Option B: Cookie string (no Chrome needed)**
```bash
# Copy cookie from browser DevTools → Network → any request → Headers → Cookie
python3 sync.py --cookie "ct0=xxx; auth_token=xxx"

# Or set as environment variable
export BIRDSEED_COOKIE="ct0=xxx; auth_token=xxx"
python3 sync.py
```

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

Each bookmark becomes a Markdown note, named by post date + cleaned title:

```
2026-03-25-How-To-Make-Obsidian-Beautiful.md
2026-03-26-Building-a-Personal-Knowledge-Base-with-LLM.md
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
tags: [x-bookmark, Productivity, Tutorial, Open Source]
---

> **Summary:** AI-generated summary here...

Full article content...

---

**Chinese Translation:** (auto-translated for English content)

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
| Summary | ❌ | ✅ 3-5 sentence summary |
| Tags | `[x-bookmark]` only | ✅ Auto-classified (AI, Design, etc.) |
| Translation | ❌ | ✅ English → Chinese (configurable) |

birdseed works perfectly fine without AI — you just won't get summaries, smart tags, or translations.

## All Options

```bash
python3 sync.py --help
```

| Flag | Description |
|------|-------------|
| `--cookie STRING` | X cookie string (alternative to login.py) |
| `--output-dir PATH` | Output directory (default: `~/birdseed-output`) |
| `--language en\|zh` | Output language for AI & labels (default: `en`) |
| `--limit N` | Max bookmarks to fetch (default: 200) |
| `--fetch-articles` / `--no-fetch-articles` | Extract external article text (default: on) |
| `--download-media` / `--no-download-media` | Download images locally (default: on) |
| `--rewrite-visible` | Rewrite all visible bookmarks |
| `--update-existing` | Add missing summary/tags/translation to existing notes |
| `-v`, `--verbose` | Debug logging |
| `-q`, `--quiet` | Warnings only |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Gemini API key (preferred for AI) |
| `ANTHROPIC_API_KEY` | Claude API key (fallback) |
| `BIRDSEED_COOKIE` | X cookie string (alternative to login.py) |
| `BIRDSEED_GEMINI_MODEL` | Gemini model override (default: `gemini-2.0-flash`) |
| `BIRDSEED_CLAUDE_MODEL` | Claude model override (default: `claude-sonnet-4-6`) |
| `CHROME_BIN` | Chrome path override for login.py |

You can also set language permanently in `data/config.json`:
```json
{
  "language": "zh"
}
```

## How It Works

```
1. Load cookies from data/storage_state.json
2. Fetch bookmarks via X GraphQL API (/i/api/graphql/.../Bookmarks)
3. For X Articles → fetch full text via TweetDetail API
4. Resolve t.co short URLs
5. Extract external article text (trafilatura, optional)
6. AI: generate summary + tags in one call
7. AI: translate content (English→Chinese by default, configurable)
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

If you use [OpenClaw](https://github.com/openclaw/openclaw), add birdseed as a cron job:

```bash
# ~/.openclaw/openclaw.json → cron → jobs
{
  "name": "X Bookmarks Sync",
  "schedule": { "kind": "cron", "expr": "0 10 * * *", "tz": "Asia/Shanghai" },
  "payload": {
    "kind": "agentTurn",
    "model": "google/gemini-3-flash-preview",
    "message": "Run `python3 ~/Projects/birdseed/sync.py --quiet`, then report new bookmarks count and any errors. Exit code 2 = cookie expired, tell me to re-run `python3 ~/Projects/birdseed/login.py`."
  }
}
```

Or add to `HEARTBEAT.md` for periodic checks with a 48-hour throttle.

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

**Q: Can I change the output language?**
A: Yes! Use `--language zh` for Chinese output (summary, tags, labels all in Chinese), or `--language en` for English (default). Set it permanently in `data/config.json` with `{"language": "zh"}`.


## 中文说明

把 X（Twitter）收藏夹同步到 Obsidian 的本地工具。本地、增量、带 AI 增强。

```
收藏夹 → GraphQL API → AI 增强 → Obsidian Markdown
```

### 为什么用 birdseed？

- **不靠浏览器自动化** — 用 X 内部 GraphQL API，不是 Playwright/Selenium 爬取
- **支持 X Article** — 长文完整提取正文（不是只有 t.co 短链接）
- **AI 增强** — 自动摘要、分类标签、可选英译中（Gemini / Claude）
- **增量同步** — 只同步新收藏，已处理的记录在 state.json
- **本地私密** — 数据只存你自己的机器，不上传任何第三方
- **开箱即用** — 登录一次就能跑

### 快速开始

```bash
# 1. 拉代码
git clone https://github.com/kirozeng/birdseed.git
cd birdseed

# 2. 装依赖（仅 login 用）
pip install playwright
python -m playwright install chromium

# 可选：外链文章提取
pip install trafilatura lxml_html_clean

# 3. 登录 X
python3 login.py
# 会打开 Chrome，手动登录（支持 Google SSO/二步验证）
# 收藏页加载后按回车，cookie 自动保存

# 4. 同步
python3 sync.py --language zh
```

### 配置中文输出

在 `data/config.json` 里写中文输出，不用每次传 flag：

```json
{
  "language": "zh",
  "output_dir": "/你的/Obsidian/Vault/X Bookmarks"
}
```

设好后 `python3 sync.py` 自动用中文输出：摘要、标签、标签内容都是中文，英文内容附中文翻译。

### AI 增强（可选）

```bash
# Gemini（推荐，有免费额度）
export GEMINI_API_KEY="your-key"

# 或 Claude
export ANTHROPIC_API_KEY="your-key"
```

没 AI key 也能跑，只是没有摘要、智能标签和翻译。

### 常用命令

```bash
python3 sync.py                    # 普通增量同步
python3 sync.py -q                 # 安静模式（cron 用）
python3 sync.py -v                 # 详细日志
python3 sync.py --limit 50         # 限制拉取数量
python3 sync.py --rewrite-visible  # 重写所有可见收藏
python3 sync.py --update-existing  # 给已有笔记补摘要/标签/翻译
```

### Cookie 过期

Cookie 能擑几周到几个月。过期时 `sync.py` exit code 2，重新跑 `python3 login.py` 就行。

### OpenClaw 集成

如果你用 [OpenClaw](https://github.com/openclaw/openclaw)，加个 cron：

```bash
# ~/.openclaw/openclaw.json → cron → jobs
{
  "name": "X Bookmarks Sync",
  "schedule": { "kind": "cron", "expr": "0 10 * * *", "tz": "Asia/Shanghai" },
  "payload": {
    "kind": "agentTurn",
    "model": "google/gemini-3-flash-preview",
    "message": "执行 X 收藏夹同步：运行 `python3 ~/Projects/birdseed/sync.py --quiet`，然后汇报结果。exit code 2 = cookie 过期。"
  }
}
```

### FAQ

**Q: 会违反 X 的 ToS 吗？**
A: 用的是 X 自己的内部 web API + 你自己的 cookie——跟浏览器发的请求一模一样。只读自己的收藏，不做任何写操作。理性使用。

**Q: 不用 Obsidian 可以吗？**
A: 可以。输出是标准 Markdown，任何笔记软件都能用。

**Q: X Article 是什么？**
A: X 的长文功能（不限 280 字）。birdseed 会另外调用一次 TweetDetail API 拿完整文本。

---

## License

MIT

## Credits

Inspired by [x2o](https://github.com/kiki123124/x2o) for the GraphQL API approach.

Built with 🐦 and 🤖 by Kiro Zeng.
