#!/usr/bin/env python3
"""Unit tests for birdseed sync.py — no network dependency."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync as sync_module
from sync import (
    is_noise_content,
    extract_main_content,
    is_primarily_english,
    clean_title,
    slugify,
    parse_twitter_date,
    normalize_text,
    set_language,
)


def test_is_noise_content():
    assert is_noise_content("回复 @user: 谢谢！") == True
    assert is_noise_content("Replying to @user") == True
    assert is_noise_content("@username") == True
    assert is_noise_content("...") == True
    assert is_noise_content("hi") == True
    assert is_noise_content("") == True
    assert is_noise_content(None) == True

    assert is_noise_content("This is a meaningful tweet about AI technology.") == False
    assert is_noise_content("今天天气真好，分享一些想法...") == False
    print("✓ Noise detection tests passed")


def test_extract_main_content():
    text = """Replying to @user
This is the actual content of the tweet.

It has multiple paragraphs."""

    result = extract_main_content(text)
    assert "Replying to" not in result
    assert "actual content" in result
    assert "multiple paragraphs" in result

    assert extract_main_content("") == ""
    assert extract_main_content(None) == ""
    print("✓ Main content extraction tests passed")


def test_extract_main_content_photo_cleanup():
    """Photo/video URLs should be removed from text."""
    text = "推荐一个好博主 https://twitter.com/user/status/123/photo/1"
    result = extract_main_content(text)
    assert "photo/1" not in result
    assert "推荐一个好博主" in result

    text2 = "看这个视频 https://x.com/user/status/456/video/1"
    result2 = extract_main_content(text2)
    assert "video/1" not in result2
    assert "看这个视频" in result2

    # Bare t.co at end of line
    text3 = "一条推文 https://t.co/abc123"
    result3 = extract_main_content(text3)
    assert "t.co" not in result3
    assert "一条推文" in result3
    print("✓ Photo/video URL cleanup tests passed")


def test_is_primarily_english():
    assert is_primarily_english("This is an English tweet about technology.") == True
    assert is_primarily_english("这是一个中文推文") == False
    assert is_primarily_english("今天分享一个 AI 工具") == False
    assert is_primarily_english("") == False
    print("✓ English detection tests passed")


def test_clean_title():
    # English mode (default)
    set_language("en")
    assert clean_title("Replying to @user: Actual Title Here") == "Actual Title Here"
    assert clean_title("回复 @user: 实际标题内容") == "实际标题内容"
    assert clean_title("") == "Untitled Bookmark"
    assert clean_title("https://t.co/abc123") == "Untitled Bookmark"
    assert clean_title("hi") == "Untitled Bookmark"
    assert "Claude" in clean_title("Claude + Obsidian 最猛方案")

    # Chinese mode
    set_language("zh")
    assert clean_title("") == "未命名收藏"
    assert clean_title("hi") == "未命名收藏"

    # Reset to default
    set_language("en")
    print("✓ Title cleaning tests passed")


def test_slugify():
    result = slugify("Test Title!!!", limit=20)
    assert "Test" in result and "Title" in result

    result2 = slugify("中文标题测试", limit=20)
    assert "中文标题测试" in result2

    assert slugify("") == "bookmark"
    assert slugify(None) == "bookmark"
    print("✓ Slugify tests passed")


def test_parse_twitter_date():
    # Standard Twitter format
    assert parse_twitter_date("Mon Mar 25 12:00:00 +0000 2026") == "2026-03-25"
    # Already ISO
    assert parse_twitter_date("2026-03-25") == "2026-03-25"
    assert parse_twitter_date("2026-03-25T12:00:00Z") == "2026-03-25"
    # Empty
    result = parse_twitter_date("")
    assert len(result) == 10 and result[4] == "-"  # returns today's date
    print("✓ Twitter date parsing tests passed")


def test_filename_format():
    text = "试了200多个龙虾OpenClaw技能，只留了这10个必装的"
    date_part = "2026-03-20"
    title_part = slugify(clean_title(text), limit=60)
    filename = f"{date_part}-{title_part}.md"

    assert filename.startswith("2026-03-20-")
    assert filename.endswith(".md")
    assert "龙虾" in filename or "OpenClaw" in filename
    print(f"✓ Filename format test passed: {filename}")


def test_normalize_text():
    assert normalize_text("hello\n\n\n\nworld") == "hello\n\nworld"
    assert normalize_text("  spaces  ") == "spaces"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""
    print("✓ Normalize text tests passed")


def test_merge_frontmatter_tags():
    content = """---
source: x-bookmarks
tags: [x-bookmark]
---

Body
"""

    updated, added = sync_module._merge_frontmatter_tags(
        content, ["AI", "Open Source", "AI"]
    )
    assert added == ["AI", "Open-Source"]
    assert "tags: [x-bookmark, AI, Open-Source]" in updated

    updated_again, added_again = sync_module._merge_frontmatter_tags(updated, ["AI"])
    assert added_again == []
    assert updated_again == updated
    print("✓ Frontmatter tag merge tests passed")


def test_update_existing_notes_adds_summary_and_tags():
    set_language("en")
    body = "这是一段关于 AI、Obsidian 和知识管理的中文内容。" * 30

    old_get_ai_key = sync_module._get_ai_key
    old_generate_summary_and_tags = sync_module.generate_summary_and_tags
    try:
        sync_module._get_ai_key = lambda: ("gemini", "fake-key")
        sync_module.generate_summary_and_tags = lambda text: (
            "生成摘要",
            ["AI", "Open Source"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            note_path = output_dir / "2026-03-25-Test.md"
            note_path.write_text(
                f"""---
source: x-bookmarks
tweet_id: "tweet-1"
date: 2026-03-25
tags: [x-bookmark]
---

{body}
""",
                encoding="utf-8",
            )

            assert sync_module._update_existing_notes(output_dir) == 0
            updated = note_path.read_text(encoding="utf-8")
            assert "> **Summary:** 生成摘要" in updated
            assert "tags: [x-bookmark, AI, Open-Source]" in updated
            assert (output_dir / "X Bookmarks Index.md").exists()
    finally:
        sync_module._get_ai_key = old_get_ai_key
        sync_module.generate_summary_and_tags = old_generate_summary_and_tags
    print("✓ Existing note enrichment tests passed")


def test_main_only_marks_written_items_seen():
    old_argv = sys.argv[:]
    old_state_path = sync_module.STATE_PATH
    old_config_path = sync_module.CONFIG_PATH
    old_load_cookie_string = sync_module.load_cookie_string
    old_fetch_bookmarks_graphql = sync_module.fetch_bookmarks_graphql
    old_enrich_x_articles = sync_module.enrich_x_articles
    old_resolve_tco_urls_in_items = sync_module.resolve_tco_urls_in_items
    old_extract_external_urls = sync_module._extract_external_urls
    old_generate_summary_and_tags = sync_module.generate_summary_and_tags

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            output_dir = tmp_dir / "output"
            sync_module.STATE_PATH = tmp_dir / "state.json"
            sync_module.CONFIG_PATH = tmp_dir / "config.json"

            good_item = {
                "tweetId": "good-1",
                "url": "https://x.com/user/status/good-1",
                "authorName": "User",
                "authorHandle": "user",
                "postedAt": "2026-03-25",
                "fullText": "This is a meaningful bookmark about AI and Obsidian notes.",
                "media": [],
                "metrics": {},
            }
            noise_item = {
                "tweetId": "noise-1",
                "url": "https://x.com/user/status/noise-1",
                "authorName": "User",
                "authorHandle": "user",
                "postedAt": "2026-03-25",
                "fullText": "hi",
                "media": [],
                "metrics": {},
            }

            sync_module.load_cookie_string = lambda cli_cookie=None: "ct0=fake"
            sync_module.fetch_bookmarks_graphql = lambda cookie_str, limit=200: [
                good_item,
                noise_item,
            ]
            sync_module.enrich_x_articles = lambda items, cookie_str: None
            sync_module.resolve_tco_urls_in_items = lambda items: None
            sync_module._extract_external_urls = lambda items: [
                item.setdefault("externalUrls", []) for item in items
            ]
            sync_module.generate_summary_and_tags = lambda text: ("", [])

            sys.argv = [
                "sync.py",
                "--output-dir",
                str(output_dir),
                "--no-fetch-articles",
                "--no-download-media",
            ]

            assert sync_module.main() == 0
            state = json.loads(sync_module.STATE_PATH.read_text(encoding="utf-8"))
            assert "good-1" in state["seen"]
            assert "noise-1" not in state["seen"]
            assert len(list(output_dir.glob("2026-03-25-*.md"))) == 1
    finally:
        sys.argv = old_argv
        sync_module.STATE_PATH = old_state_path
        sync_module.CONFIG_PATH = old_config_path
        sync_module.load_cookie_string = old_load_cookie_string
        sync_module.fetch_bookmarks_graphql = old_fetch_bookmarks_graphql
        sync_module.enrich_x_articles = old_enrich_x_articles
        sync_module.resolve_tco_urls_in_items = old_resolve_tco_urls_in_items
        sync_module._extract_external_urls = old_extract_external_urls
        sync_module.generate_summary_and_tags = old_generate_summary_and_tags
    print("✓ State update skips unwritten items tests passed")


if __name__ == "__main__":
    test_is_noise_content()
    test_extract_main_content()
    test_extract_main_content_photo_cleanup()
    test_is_primarily_english()
    test_clean_title()
    test_slugify()
    test_parse_twitter_date()
    test_filename_format()
    test_normalize_text()
    test_merge_frontmatter_tags()
    test_update_existing_notes_adds_summary_and_tags()
    test_main_only_marks_written_items_seen()
    print("\n✅ All tests passed!")
