#!/usr/bin/env python3
"""birdseed — Sync X (Twitter) bookmarks to Obsidian via GraphQL API.

https://github.com/kirozeng/birdseed

Data flow:
  1. Fetch bookmarks via X's internal GraphQL API (cookie auth)
  2. Detect X Articles → fetch full plain_text via TweetDetail API
  3. Resolve t.co short URLs → extract external URLs
  4. Optionally fetch external article text (trafilatura)
  5. AI enrichment: summary, auto-tags, English→Chinese translation
  6. Write Obsidian-compatible Markdown notes with frontmatter
  7. Download tweet images via HTTP
  8. Generate MOC index

Dependencies:
- Required: Python 3.9+, data/storage_state.json (from login.py)
- Optional: trafilatura (external article extraction)
- Optional: GEMINI_API_KEY or ANTHROPIC_API_KEY (AI enrichment)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

try:
    import trafilatura
except ImportError:
    trafilatura = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("x-sync")


def setup_logging(verbose: bool = False, quiet: bool = False):
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / "state.json"
CONFIG_PATH = DATA_DIR / "config.json"
STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"

FALLBACK_OUTPUT_DIR = str(Path.home() / "birdseed-output")
MEDIA_DIR_NAME = "media"

NON_WORD_RE = re.compile(r"[^\w\-\u4e00-\u9fff]+", re.UNICODE)

REPLY_PATTERNS = [
    re.compile(r'^\s*回复\s*[@@]\w+[:：]', re.IGNORECASE),
    re.compile(r'^\s*Replying to\s*[@@]\w+', re.IGNORECASE),
    re.compile(r'^\s*[@@]\w+\s*$'),
    re.compile(r'^\s*\.\.\.\s*$'),
]

SHORT_URL_DOMAINS = {
    "t.co", "bit.ly", "tinyurl.com", "ow.ly",
    "goo.gl", "is.gd", "buff.ly", "lnkd.in",
}

TAG_CATEGORIES = [
    "AI", "LLM", "设计", "前端", "后端", "全栈", "DevOps", "移动开发",
    "游戏开发", "数据科学", "区块链", "产品", "创业", "效率工具",
    "开源", "教程", "观点", "新闻", "生活",
]

# --- GraphQL API constants (from X web client) ---
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
GRAPHQL_QUERY_ID = "-LGfdImKeQz0xS_jjUwzlA"
TWEET_DETAIL_QUERY_ID = "nBS-WpgA6ZG0CyNHD517JQ"

TWEET_DETAIL_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_richtext_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_media_download_video_enabled": False,
}

X_ARTICLE_RE = re.compile(r"x\.com/i/article/\d+")

GRAPHQL_FEATURES = {
    "graphql_timeline_v2_bookmark_timeline": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}


# ---------------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------------

def load_cookie_string() -> str:
    """Build cookie string from storage_state.json."""
    if not STORAGE_STATE_PATH.exists():
        raise SystemExit(
            f"Missing storage state: {STORAGE_STATE_PATH}\n"
            "Run login.py first or provide a cookie file."
        )
    data = json.loads(STORAGE_STATE_PATH.read_text(encoding="utf-8"))
    cookies = data.get("cookies", [])
    x_cookies = [
        c for c in cookies
        if any(d in c.get("domain", "") for d in ("x.com", "twitter.com"))
    ]
    if not x_cookies:
        raise SystemExit("No X/Twitter cookies found in storage_state.json")

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in x_cookies)

    # Verify ct0 exists
    ct0_cookies = [c for c in x_cookies if c["name"] == "ct0"]
    if not ct0_cookies:
        raise SystemExit("ct0 (CSRF token) not found in cookies. Re-run login.py.")

    return cookie_str


def get_csrf_token(cookie_str: str) -> str:
    """Extract ct0 from cookie string."""
    m = re.search(r"ct0=([^;]+)", cookie_str)
    if not m:
        raise SystemExit("ct0 not found in cookie string")
    return m.group(1)


# ---------------------------------------------------------------------------
# GraphQL bookmark fetching
# ---------------------------------------------------------------------------

def fetch_bookmarks_graphql(
    cookie_str: str, limit: int = 200
) -> List[Dict]:
    """Fetch bookmarks via X's internal GraphQL API."""
    csrf_token = get_csrf_token(cookie_str)
    bookmarks: List[Dict] = []
    cursor: Optional[str] = None

    while len(bookmarks) < limit:
        variables: Dict[str, Any] = {
            "count": min(20, limit - len(bookmarks)),
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor

        params = urlencode({
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(GRAPHQL_FEATURES, separators=(",", ":")),
        })
        url = f"https://x.com/i/api/graphql/{GRAPHQL_QUERY_ID}/Bookmarks?{params}"

        req = Request(url)
        req.add_header("Authorization", f"Bearer {BEARER_TOKEN}")
        req.add_header("Cookie", cookie_str)
        req.add_header("x-csrf-token", csrf_token)
        req.add_header("x-twitter-active-user", "yes")
        req.add_header("x-twitter-auth-type", "OAuth2Session")
        req.add_header("Content-Type", "application/json")
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code in (401, 403):
                log.error(
                    "Authentication failed (%d). Cookie may be expired. "
                    "Re-run login.py to refresh.", e.code
                )
                raise SystemExit(2)  # Exit code 2 = auth failure
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            log.error("X API error %d: %s", e.code, body)
            raise SystemExit(1)

        items, next_cursor = _parse_graphql_response(data)
        bookmarks.extend(items)
        log.info("Fetched %d/%d bookmarks", len(bookmarks), limit)

        if not next_cursor or not items:
            break
        cursor = next_cursor

    return bookmarks


def _parse_graphql_response(data: Dict) -> Tuple[List[Dict], Optional[str]]:
    """Parse GraphQL bookmark response into structured items."""
    timeline = (
        data.get("data", {})
        .get("bookmark_timeline_v2", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    if not timeline:
        return [], None

    add_entries = None
    for instr in timeline:
        if instr.get("type") == "TimelineAddEntries":
            add_entries = instr
            break
    if not add_entries:
        return [], None

    entries = add_entries.get("entries", [])
    items: List[Dict] = []
    next_cursor: Optional[str] = None

    for entry in entries:
        entry_id = entry.get("entryId", "")

        if entry_id.startswith("tweet-"):
            result = (
                entry.get("content", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result")
            )
            if not result:
                continue
            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet", result)

            legacy = result.get("legacy")
            if not legacy:
                continue

            rest_id = result.get("rest_id", "")
            if not rest_id:
                continue

            # User info
            user_result = (
                result.get("core", {}).get("user_results", {}).get("result", {})
            )
            user_legacy = user_result.get("legacy", {})
            screen_name = user_legacy.get("screen_name", "")
            display_name = user_legacy.get("name", "")

            # Tweet text — prefer note_tweet for long-form
            note_tweet_text = (
                result.get("note_tweet", {})
                .get("note_tweet_results", {})
                .get("result", {})
                .get("text")
            )
            full_text = note_tweet_text or legacy.get("full_text", "")

            # Media
            media_list = []
            for m in legacy.get("extended_entities", {}).get("media", []):
                media_type = m.get("type", "photo")
                if media_type == "video":
                    # Get highest bitrate video URL
                    variants = m.get("video_info", {}).get("variants", [])
                    video_variants = [
                        v for v in variants if v.get("content_type") == "video/mp4"
                    ]
                    video_variants.sort(
                        key=lambda v: v.get("bitrate", 0), reverse=True
                    )
                    video_url = video_variants[0]["url"] if video_variants else ""
                    media_list.append({
                        "type": "video",
                        "url": m.get("media_url_https", ""),
                        "videoUrl": video_url,
                        "altText": m.get("ext_alt_text"),
                    })
                elif media_type == "animated_gif":
                    variants = m.get("video_info", {}).get("variants", [])
                    gif_url = variants[0]["url"] if variants else ""
                    media_list.append({
                        "type": "gif",
                        "url": m.get("media_url_https", ""),
                        "videoUrl": gif_url,
                        "altText": m.get("ext_alt_text"),
                    })
                else:
                    media_list.append({
                        "type": "photo",
                        "url": m.get("media_url_https", ""),
                        "altText": m.get("ext_alt_text"),
                    })

            # Metrics
            metrics = {
                "likes": legacy.get("favorite_count", 0),
                "retweets": legacy.get("retweet_count", 0),
                "replies": legacy.get("reply_count", 0),
                "bookmarks": legacy.get("bookmark_count", 0),
            }

            # Quote tweet
            quoted_tweet = None
            qt_result = result.get("quoted_status_result", {}).get("result")
            if qt_result:
                if qt_result.get("__typename") == "TweetWithVisibilityResults":
                    qt_result = qt_result.get("tweet", qt_result)
                qt_legacy = qt_result.get("legacy", {})
                qt_user = (
                    qt_result.get("core", {})
                    .get("user_results", {})
                    .get("result", {})
                    .get("legacy", {})
                )
                qt_note = (
                    qt_result.get("note_tweet", {})
                    .get("note_tweet_results", {})
                    .get("result", {})
                    .get("text")
                )
                qt_text = qt_note or qt_legacy.get("full_text", "")
                qt_handle = qt_user.get("screen_name", "")
                qt_id = qt_result.get("rest_id", "")
                if qt_text:
                    quoted_tweet = {
                        "text": qt_text,
                        "authorHandle": qt_handle,
                        "url": f"https://x.com/{qt_handle}/status/{qt_id}" if qt_handle else "",
                    }

            # Conversation thread detection
            is_reply_to_self = (
                legacy.get("in_reply_to_screen_name", "").lower() == screen_name.lower()
                and legacy.get("in_reply_to_status_id_str")
            )

            items.append({
                "tweetId": rest_id,
                "url": f"https://x.com/{screen_name}/status/{rest_id}" if screen_name else f"https://x.com/i/status/{rest_id}",
                "authorName": display_name,
                "authorHandle": screen_name,
                "postedAt": legacy.get("created_at", ""),
                "fullText": full_text,
                "media": media_list,
                "metrics": metrics,
                "quotedTweet": quoted_tweet,
                "isReplyToSelf": is_reply_to_self,
                "inReplyToStatusId": legacy.get("in_reply_to_status_id_str"),
                "conversationId": legacy.get("conversation_id_str", rest_id),
            })

        elif entry_id.startswith("cursor-bottom-"):
            next_cursor = entry.get("content", {}).get("value")

    return items, next_cursor


# ---------------------------------------------------------------------------
# X Article fetching via TweetDetail API
# ---------------------------------------------------------------------------

def fetch_x_article(cookie_str: str, tweet_id: str) -> Dict:
    """Fetch X Article content via TweetDetail GraphQL endpoint.
    Returns dict with 'title' and 'plainText' if found."""
    csrf_token = get_csrf_token(cookie_str)
    variables = json.dumps({
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
    }, separators=(",", ":"))
    features = json.dumps(TWEET_DETAIL_FEATURES, separators=(",", ":"))
    field_toggles = json.dumps({"withArticlePlainText": True}, separators=(",", ":"))

    params = urlencode({
        "variables": variables,
        "features": features,
        "fieldToggles": field_toggles,
    })
    url = f"https://x.com/i/api/graphql/{TWEET_DETAIL_QUERY_ID}/TweetDetail?{params}"

    req = Request(url)
    req.add_header("Authorization", f"Bearer {BEARER_TOKEN}")
    req.add_header("Cookie", cookie_str)
    req.add_header("x-csrf-token", csrf_token)
    req.add_header("x-twitter-active-user", "yes")
    req.add_header("x-twitter-auth-type", "OAuth2Session")
    req.add_header("Content-Type", "application/json")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("TweetDetail fetch failed for %s: %s", tweet_id, e)
        return {}

    instructions = (
        data.get("data", {})
        .get("threaded_conversation_with_injections_v2", {})
        .get("instructions", [])
    )
    for instr in instructions:
        for entry in instr.get("entries", []):
            if entry.get("entryId", "").startswith("tweet-"):
                result = (
                    entry.get("content", {})
                    .get("itemContent", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                if result.get("__typename") == "TweetWithVisibilityResults":
                    result = result.get("tweet", result)

                # Check for article
                article = (
                    result.get("article", {})
                    .get("article_results", {})
                    .get("result", {})
                )
                if article:
                    return {
                        "title": article.get("title", ""),
                        "plainText": article.get("plain_text", ""),
                        "previewText": article.get("preview_text", ""),
                    }

                # Also check note_tweet (long-form tweets)
                note = (
                    result.get("note_tweet", {})
                    .get("note_tweet_results", {})
                    .get("result", {})
                )
                if note.get("text"):
                    return {
                        "title": "",
                        "plainText": note["text"],
                    }
    return {}


def enrich_x_articles(items: List[Dict], cookie_str: str) -> None:
    """For items that link to X Articles, fetch the full article content."""
    for item in items:
        full_text = item.get("fullText", "")
        # Detect X Article links
        if X_ARTICLE_RE.search(full_text) or (
            len(full_text.strip()) < 50 and "t.co" in full_text
        ):
            log.info("Fetching X Article for tweet %s...", item["tweetId"])
            article = fetch_x_article(cookie_str, item["tweetId"])
            if article:
                title = article.get("title", "")
                plain = article.get("plainText", "")
                if plain:
                    item["fullText"] = plain
                    if title:
                        item["articleTitle"] = title
                    log.info(
                        "X Article: '%s' (%d chars)",
                        title[:50] if title else "untitled",
                        len(plain),
                    )
            # Rate limit: small delay between article fetches
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# t.co URL resolution (batch, concurrent-ish)
# ---------------------------------------------------------------------------

TCO_RE = re.compile(r"https?://t\.co/\w+")


def resolve_short_url(url: str, max_redirects: int = 5) -> str:
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname not in SHORT_URL_DOMAINS:
        return url
    try:
        from urllib.request import build_opener, HTTPRedirectHandler

        class _NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = build_opener(_NoRedirect)
        current = url
        for _ in range(max_redirects):
            req = Request(current, method="HEAD")
            req.add_header("User-Agent", "Mozilla/5.0")
            try:
                opener.open(req, timeout=10)
                break
            except Exception as e:
                loc = (
                    getattr(e, "headers", {}).get("Location")
                    if hasattr(e, "headers")
                    else None
                )
                if loc:
                    current = loc
                else:
                    break
        return current
    except Exception:
        return url


def resolve_tco_urls_in_items(items: List[Dict]) -> None:
    """Batch-resolve t.co URLs in all items' fullText."""
    url_set: set = set()
    for item in items:
        for m in TCO_RE.finditer(item.get("fullText", "")):
            url_set.add(m.group(0))

    if not url_set:
        return

    log.info("Resolving %d t.co short URLs...", len(url_set))
    url_map: Dict[str, str] = {}
    for url in url_set:
        resolved = resolve_short_url(url)
        if resolved != url:
            url_map[url] = resolved

    for item in items:
        text = item.get("fullText", "")
        for short, real in url_map.items():
            text = text.replace(short, real)
        item["fullText"] = text

    log.info("Resolved %d/%d short URLs", len(url_map), len(url_set))


def _extract_external_urls(items: List[Dict]) -> None:
    """Build externalUrls list from all URLs in fullText, excluding X/Twitter links."""
    url_re = re.compile(r"https?://\S+")
    skip_domains = {"x.com", "twitter.com", "t.co"}
    for item in items:
        urls = []
        for m in url_re.finditer(item.get("fullText", "")):
            url = m.group(0).rstrip(",.;:!?)>」）】")
            parsed = urlparse(url)
            if parsed.hostname and not any(d in parsed.hostname for d in skip_domains):
                if url not in urls:
                    urls.append(url)
        item["externalUrls"] = urls


# ---------------------------------------------------------------------------
# Config & state (unchanged from v2)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_output_dir(cli_override: Optional[str] = None) -> Path:
    if cli_override:
        return Path(cli_override)
    cfg = load_config()
    if cfg.get("output_dir"):
        return Path(cfg["output_dir"])
    return Path(FALLBACK_OUTPUT_DIR)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 2, "lastRun": None, "seen": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (value or "").strip())


def clean_title(text: str, limit: int = 50) -> str:
    """Extract a readable title from tweet text."""
    if not text:
        return "未命名收藏"
    # Remove URLs
    cleaned = re.sub(r"https?://\S+", "", text).strip()
    # Remove @mentions at the start
    cleaned = re.sub(r'^[@@]\w+\s*', '', cleaned).strip()
    # Remove reply patterns
    cleaned = re.sub(
        r'^(Replying to|回复)\s*[@@]?\w+[:：]?\s*', '', cleaned, flags=re.IGNORECASE
    ).strip()
    # Take first meaningful line
    for line in cleaned.splitlines():
        line = line.strip()
        if line and len(line) > 5:
            # Clean and truncate
            title = re.sub(r"\s+", " ", line).strip(" -—_·")
            return title[:limit] if title else "未命名收藏"
    return "未命名收藏"


def slugify(text: str, limit: int = 60) -> str:
    text = NON_WORD_RE.sub("-", (text or "").strip()).strip("-")
    return text[:limit] or "bookmark"


def is_noise_content(text: str) -> bool:
    if not text:
        return True
    text_stripped = text.strip()
    for pattern in REPLY_PATTERNS:
        if pattern.match(text_stripped):
            return True
    if len(text_stripped) < 10:
        return True
    return False


def extract_main_content(text: str) -> str:
    if not text:
        return ""
    lines = text.split("\n")
    main_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(p.match(line) for p in REPLY_PATTERNS):
            continue
        main_lines.append(line)
    result = normalize_text("\n\n".join(main_lines))
    # Clean up photo/video URLs from tweet text (anywhere in text)
    result = re.sub(
        r"\s*https?://(?:twitter\.com|x\.com)/\S+/(?:photo|video)/\d+",
        "", result,
    ).strip()
    # Clean up bare t.co links that resolved to x.com media
    result = re.sub(
        r"\s*https?://t\.co/\w+\s*$", "", result, flags=re.MULTILINE
    ).strip()
    return result


def is_primarily_english(text: str) -> bool:
    if not text:
        return False
    cleaned = re.sub(r"https?://\S+", "", text)
    cleaned = re.sub(r"[@#]\w+", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        return False
    ascii_chars = sum(1 for c in cleaned if ord(c) < 128 and c.isalpha())
    total_chars = sum(1 for c in cleaned if c.isalpha())
    if total_chars == 0:
        return False
    return (ascii_chars / total_chars) > 0.6


def parse_twitter_date(date_str: str) -> str:
    """Parse Twitter's date format to YYYY-MM-DD."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        # Twitter format: "Mon Jan 01 12:00:00 +0000 2026"
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        # Already ISO format
        if len(date_str) >= 10 and date_str[4] == "-":
            return date_str[:10]
        return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Network helpers for article extraction
# ---------------------------------------------------------------------------

def _fetch_article_trafilatura(url: str) -> str:
    if trafilatura is None:
        return ""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    extracted = trafilatura.extract(
        downloaded, include_links=False, include_images=False, output_format="txt"
    )
    if extracted and len(extracted.strip()) > 100:
        text = normalize_text(extracted)
        # Truncate overly long articles
        if len(text) > 5000:
            text = text[:5000] + "\n\n[...内容已截断]"
        return text
    return ""


def fetch_external_article(url: str, max_retries: int = 1) -> str:
    """Extract article text with trafilatura. Truncated to reasonable length."""
    resolved = resolve_short_url(url)
    # Skip media/social URLs
    skip_domains = {"x.com", "twitter.com", "youtube.com", "youtu.be", "instagram.com", "github.com"}
    parsed = urlparse(resolved)
    if parsed.hostname and any(d in parsed.hostname for d in skip_domains):
        return ""

    for attempt in range(1 + max_retries):
        try:
            text = _fetch_article_trafilatura(resolved)
            if text:
                log.debug("Article OK: %d chars from %s", len(text), resolved)
                return text
        except Exception as e:
            log.debug("Article extraction error (attempt %d): %s", attempt + 1, e)

        if attempt < max_retries:
            time.sleep(2.0 * (attempt + 1))

    return ""


def download_image_http(img_url: str, media_dir: Path) -> Optional[Path]:
    """Download image via HTTP (no Playwright needed)."""
    url_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]
    parsed = urlparse(img_url)
    ext = Path(parsed.path).suffix or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    filename = f"{url_hash}{ext}"
    local_path = media_dir / filename
    if local_path.exists():
        return local_path

    for attempt in range(2):
        try:
            req = Request(img_url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urlopen(req, timeout=30) as resp:
                local_path.write_bytes(resp.read())
            return local_path
        except Exception as e:
            log.debug("Image download attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# AI helper (Gemini primary, Claude fallback)
# ---------------------------------------------------------------------------

def _get_ai_key() -> tuple:
    """Returns (provider, api_key). Prefers Gemini, falls back to Claude."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        return ("gemini", gemini_key)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return ("anthropic", anthropic_key)
    return ("none", "")


def _call_gemini(prompt: str, api_key: str, timeout: int = 90) -> str:
    """Call Gemini API."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-2.0-flash:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    try:
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        return text.strip()
    except Exception as e:
        log.warning("Gemini call failed: %s", e)
    return ""


def _call_claude(prompt: str, api_key: str, timeout: int = 90) -> str:
    """Call Claude API."""
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for block in data.get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                return block["text"].strip()
    except Exception as e:
        log.warning("Claude call failed: %s", e)
    return ""


def _call_ai(prompt: str, timeout: int = 90) -> str:
    """Call AI with auto-detected provider."""
    provider, key = _get_ai_key()
    if provider == "none":
        log.warning("No AI API key found — skipping AI enrichment")
        return ""
    if provider == "gemini":
        return _call_gemini(prompt, key, timeout)
    return _call_claude(prompt, key, timeout)


def translate_to_chinese(text: str) -> str:
    result = _call_ai(
        "Translate the following English content into natural, fluent Simplified "
        "Chinese. Keep formatting, section structure, command lines, and links "
        "intact. Output translation only, no explanation.\n\n"
        + text[:15000]
    )
    if result:
        log.info("Translation: OK (%d chars)", len(result))
    return result


def generate_summary(text: str) -> str:
    if len(text) < 300:
        return ""
    result = _call_ai(
        "用中文写一段 3-5 句话的摘要概括以下内容的核心观点。直接输出摘要，不要任何前缀。\n\n"
        + text[:15000]
    )
    if result:
        log.info("Summary: OK (%d chars)", len(result))
    return result


def generate_tags(text: str) -> List[str]:
    cats = ", ".join(TAG_CATEGORIES)
    raw = _call_ai(
        f"根据以下内容，从这些类别中选择 1-3 个最相关的标签：{cats}\n"
        "只输出标签，用英文逗号分隔，不要加 # 号。如果都不合适可以创建新标签（中文）。\n\n"
        + text[:3000]
    )
    if not raw:
        return []
    tags = [t.strip().strip("#") for t in re.split(r"[,，、\n]", raw) if t.strip()]
    log.info("Tags: %s", tags[:3])
    return tags[:3]


def generate_summary_and_tags(text: str) -> Tuple[str, List[str]]:
    """Generate summary + tags in a single AI call. Falls back to separate calls on parse failure."""
    if len(text) < 300:
        # Too short for summary, just get tags
        return "", generate_tags(text) if len(text) > 50 else []

    cats = ", ".join(TAG_CATEGORIES)
    raw = _call_ai(
        "根据以下内容，完成两个任务：\n\n"
        "任务1 - 摘要：用中文写 3-5 句话概括核心观点。\n"
        f"任务2 - 标签：从这些类别中选 1-3 个最相关的：{cats}（如果都不合适可以创建新标签）\n\n"
        "严格按以下格式输出，不要加其他内容：\n"
        "摘要：<你的摘要>\n"
        "标签：<标签1>, <标签2>, <标签3>\n\n"
        + text[:15000]
    )
    if not raw:
        return "", []

    summary = ""
    tags: List[str] = []

    for line in raw.strip().split("\n"):
        line = line.strip()
        if line.startswith("摘要：") or line.startswith("摘要:"):
            summary = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("标签：") or line.startswith("标签:"):
            tag_str = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            tags = [t.strip().strip("#") for t in re.split(r"[,，、]", tag_str) if t.strip()][:3]

    if summary and tags:
        log.info("Summary+Tags: OK (%d chars, %s)", len(summary), tags)
        return summary, tags

    # Parse failed, fall back to separate calls
    log.debug("Combined parse failed, falling back to separate calls")
    if not summary:
        summary = generate_summary(text)
    if not tags:
        tags = generate_tags(text)
    return summary, tags


# ---------------------------------------------------------------------------
# Note building
# ---------------------------------------------------------------------------

def build_note_content(
    item: Dict, synced_at: str, local_media_paths: List[str]
) -> str:
    full_text = item.get("fullText") or ""
    article_text = item.get("articleText") or ""
    article_title = item.get("articleTitle") or ""
    quoted_tweet = item.get("quotedTweet")
    video_urls = item.get("videoUrls") or []
    summary = item.get("summary") or ""
    auto_tags = item.get("autoTags") or []
    metrics = item.get("metrics") or {}

    full_text = extract_main_content(full_text)

    author_name = item.get("authorName") or "Unknown"
    author_handle = item.get("authorHandle") or "unknown"
    tweet_url = item.get("url") or ""
    posted_at = item.get("postedAt") or ""
    tweet_id = item.get("tweetId") or ""
    article_source_url = item.get("articleSourceUrl") or ""
    date_str = parse_twitter_date(posted_at)

    is_link_tweet = (
        bool(item.get("externalUrls"))
        and len(full_text) < 200
    )

    # --- Tags ---
    tags = ["x-bookmark"]
    if article_text:
        tags.append("article")
    if video_urls:
        tags.append("video")
    if quoted_tweet:
        tags.append("quote")
    for t in auto_tags:
        safe = t.replace(" ", "-")
        if safe and safe not in tags:
            tags.append(safe)
    tags_str = "[" + ", ".join(tags) + "]"

    # --- Frontmatter ---
    fm = [
        "---",
        "source: x-bookmarks",
        f'author: "@{author_handle}"',
        f'author_name: "{author_name}"',
        f'tweet_id: "{tweet_id}"',
        f'tweet_url: "{tweet_url}"',
        f"date: {date_str}",
        f'synced_at: "{synced_at}"',
    ]
    if article_title:
        fm.append(f'title: "{article_title}"')
    if article_source_url:
        fm.append(f'article_url: "{article_source_url}"')
    if metrics:
        fm.append(f'likes: {metrics.get("likes", 0)}')
        fm.append(f'retweets: {metrics.get("retweets", 0)}')
        fm.append(f'replies: {metrics.get("replies", 0)}')
    fm.extend([f"tags: {tags_str}", "---", ""])

    lines: List[str] = []

    # --- Summary ---
    if summary:
        lines.extend([f"> **摘要：** {summary.replace(chr(10), ' ')}", ""])

    # --- Body ---
    if article_text and is_link_tweet:
        # Link-sharing tweet: show article as main content
        if full_text:
            for fl in full_text.split("\n"):
                lines.append(f"> {fl}")
            lines.append("")
        lines.append(article_text)
    elif article_text:
        lines.append(full_text or "")
        lines.extend(["", "---", "", "**引用文章：**", "", article_text])
    else:
        lines.append(full_text or "")

    # --- Quoted tweet ---
    if quoted_tweet:
        qt_text = quoted_tweet.get("text") or ""
        qt_url = quoted_tweet.get("url") or ""
        qt_author = quoted_tweet.get("authorHandle") or ""
        lines.extend(["", "---", ""])
        if qt_author:
            lines.append(f"**引用 @{qt_author}：**")
        else:
            lines.append("**引用推文：**")
        lines.append("")
        if qt_url:
            lines.append(f"> [{qt_url}]({qt_url})")
        for ql in qt_text.split("\n"):
            lines.append(f"> {ql}")

    # --- Translation ---
    text_for_tl = article_text or full_text
    if is_primarily_english(text_for_tl):
        tl = translate_to_chinese(text_for_tl)
        if tl:
            lines.extend(["", "---", "", "**中文翻译：**", "", tl])

    # --- Video ---
    if video_urls:
        lines.extend(["", "---", "", "**视频：**"])
        lines.append(f"- [在 X 上观看]({tweet_url})")
        for vu in video_urls:
            if vu != tweet_url and not vu.startswith("blob:"):
                lines.append(f"- {vu}")

    # --- Metrics ---
    if metrics and any(metrics.get(k, 0) > 0 for k in ("likes", "retweets", "replies")):
        lines.extend(["", "---", ""])
        lines.append(
            f"❤️ {metrics.get('likes', 0)} "
            f"🔁 {metrics.get('retweets', 0)} "
            f"💬 {metrics.get('replies', 0)}"
        )

    # --- Media ---
    if local_media_paths:
        lines.extend(["", "---", ""])
        for mp in local_media_paths:
            lines.append(f"![[{mp}]]")

    # --- Links ---
    ext_urls = item.get("externalUrls", [])
    if ext_urls:
        lines.extend(["", "---", "", "**Links:**"])
        for url in ext_urls[:10]:
            lines.append(f"- {url}")

    lines.append("")
    return "\n".join(fm + lines)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_note_files(
    items: List[Dict], output_dir: Path, synced_at: str
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / MEDIA_DIR_NAME
    media_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    # Build index of existing notes by tweet_id for overwrite support
    existing_by_id: Dict[str, Path] = {}
    for note_path in output_dir.glob("2*.md"):
        fm = _parse_frontmatter(note_path)
        if fm and fm.get("tweet_id"):
            existing_by_id[fm["tweet_id"]] = note_path

    for item in items:
        full_text = item.get("fullText") or ""

        if is_noise_content(full_text) and not item.get("articleText"):
            log.info("Skip noise: %s", item["tweetId"])
            continue

        # If this tweet already has a note, overwrite it (delete old file first)
        old_path = existing_by_id.get(item["tweetId"])
        if old_path and old_path.exists():
            old_path.unlink()
            log.debug("Removed old note: %s", old_path.name)

        date_str = parse_twitter_date(item.get("postedAt", ""))
        # Prefer articleTitle > clean_title from text
        raw_title = item.get("articleTitle") or clean_title(full_text)
        title_part = slugify(raw_title, limit=60)
        filename = f"{date_str}-{title_part}.md"
        path = output_dir / filename

        counter = 2
        while path.exists():
            path = output_dir / f"{date_str}-{title_part}-{counter}.md"
            counter += 1

        # Download media via HTTP (no Playwright needed)
        local_media_paths: List[str] = []
        for media in item.get("media", []):
            img_url = media.get("url", "")
            if not img_url:
                continue
            try:
                lp = download_image_http(img_url, media_dir)
                if lp:
                    local_media_paths.append(f"{MEDIA_DIR_NAME}/{lp.name}")
            except Exception as e:
                log.warning("Image failed %s: %s", img_url[:60], e)

        path.write_text(
            build_note_content(item, synced_at, local_media_paths), encoding="utf-8"
        )
        written.append(path)
        item["notePath"] = str(path)
        log.info("Wrote: %s", path.name)

    return written


# ---------------------------------------------------------------------------
# MOC (Map of Content) index
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> Optional[Dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        end_idx = text.index("---", 3)
        fm: Dict[str, str] = {}
        for line in text[3:end_idx].strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        return fm
    except Exception:
        return None


def generate_moc(output_dir: Path):
    notes = sorted(output_dir.glob("2*.md"))
    if not notes:
        return

    by_month: Dict[str, List[Dict]] = {}
    for note_path in notes:
        fm = _parse_frontmatter(note_path)
        if fm is None:
            continue

        date = fm.get("date", note_path.name[:10])
        month = date[:7]
        title = note_path.stem[11:].replace("-", " ").strip() or "未命名"
        author = fm.get("author_name", fm.get("author", ""))

        by_month.setdefault(month, []).append({
            "path": note_path.name,
            "title": title,
            "date": date,
            "author": author,
        })

    total = sum(len(v) for v in by_month.values())
    lines = [
        "# X Bookmarks Index",
        "",
        f"> {total} 条收藏 | 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    for month in sorted(by_month.keys(), reverse=True):
        entries = by_month[month]
        lines.extend([f"## {month}", ""])
        for entry in sorted(entries, key=lambda x: x["date"], reverse=True):
            author_str = f" — {entry['author']}" if entry["author"] else ""
            link = entry["path"][:-3]
            lines.append(
                f"- [[{link}|{entry['date']} {entry['title']}]]{author_str}"
            )
        lines.append("")

    idx_path = output_dir / "X Bookmarks Index.md"
    idx_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("MOC: %s (%d entries)", idx_path.name, total)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="birdseed — Sync X bookmarks to Obsidian")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max bookmarks to fetch (default: 200)"
    )
    parser.add_argument(
        "--fetch-articles", action=argparse.BooleanOptionalAction, default=True,
        help="Extract external article text (default: True)"
    )
    parser.add_argument(
        "--download-media", action=argparse.BooleanOptionalAction, default=True,
        help="Download images locally (default: True)"
    )
    parser.add_argument(
        "--rewrite-visible", action="store_true",
        help="Rewrite notes for visible bookmarks even if seen"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    parser.add_argument("--quiet", "-q", action="store_true", help="Warnings only")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    output_dir = get_output_dir(args.output_dir)

    # Check API key availability
    ai_provider, _ = _get_ai_key()
    if ai_provider != "none":
        log.info("AI provider: %s — enrichment enabled (translation, summary, tags)", ai_provider)
    else:
        log.warning(
            "No AI API key (GEMINI_API_KEY / ANTHROPIC_API_KEY) — "
            "translation, summary, and auto-tags will be skipped"
        )

    # --- Step 1: Load cookies and fetch bookmarks via GraphQL ---
    cookie_str = load_cookie_string()
    log.info("Fetching bookmarks via GraphQL API...")
    all_items = fetch_bookmarks_graphql(cookie_str, limit=args.limit)
    log.info("Fetched %d bookmarks total", len(all_items))

    # --- Step 2: Filter to new items ---
    state = load_state()
    known_ids = set((state.get("seen") or {}).keys())
    new_items = (
        all_items
        if args.rewrite_visible
        else [i for i in all_items if i["tweetId"] not in known_ids]
    )
    log.info("Total %d, new %d", len(all_items), len(new_items))

    if not new_items:
        state["lastRun"] = datetime.now().astimezone().isoformat()
        save_state(state)
        log.info("No new bookmarks.")
        return 0

    # --- Step 3: Fetch X Article content ---
    enrich_x_articles(new_items, cookie_str)

    # --- Step 4: Resolve t.co URLs & extract external URLs ---
    resolve_tco_urls_in_items(new_items)
    _extract_external_urls(new_items)

    # --- Step 5: Extract external articles ---
    if args.fetch_articles:
        for item in new_items:
            ext_urls = item.get("externalUrls", [])
            for ext_url in ext_urls[:3]:
                article = fetch_external_article(ext_url)
                if article:
                    item["articleText"] = article
                    item["articleSourceUrl"] = ext_url
                    log.info("Article: %d chars from %s", len(article), ext_url)
                    break

    # --- Step 6: Extract video URLs from media ---
    for item in new_items:
        video_urls = []
        for media in item.get("media", []):
            if media.get("type") in ("video", "gif"):
                vu = media.get("videoUrl")
                if vu:
                    video_urls.append(vu)
        if video_urls:
            item["videoUrls"] = video_urls

    # --- Step 7: AI enrichment (combined summary+tags in 1 call) ---
    for item in new_items:
        content = item.get("articleText") or item.get("fullText") or ""
        if content:
            summary, tags = generate_summary_and_tags(content)
            item["summary"] = summary
            item["autoTags"] = tags

    # --- Step 8: Write notes ---
    now_iso = datetime.now().astimezone().isoformat()
    written = write_note_files(new_items, output_dir, now_iso)

    # --- Step 9: Generate MOC ---
    generate_moc(output_dir)

    # --- Step 10: Update state ---
    seen = state.setdefault("seen", {})
    for item in new_items:
        seen[item["tweetId"]] = {
            "url": item.get("url", ""),
            "syncedAt": now_iso,
            "notePath": item.get("notePath"),
        }
    state["lastRun"] = now_iso
    state["version"] = 2
    save_state(state)

    log.info(
        "Done: fetched=%d new=%d wrote=%d",
        len(all_items), len(new_items), len(written),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
