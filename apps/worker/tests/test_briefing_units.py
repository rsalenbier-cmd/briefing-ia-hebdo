"""Tests unitaires du briefing IA : fonctions pures, sans réseau ni SDK.

Exécution : ``python -m unittest discover -s apps/worker/tests`` depuis la racine du projet
(ou via pytest : les classes unittest sont compatibles).
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from worker.workflows.briefing_utils import (  # noqa: E402
    THREAD_POSTS,
    bluesky_post_url,
    facet_for_link,
    markdown_to_notion_blocks,
    parse_rss,
    validate_posts,
)

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>OpenAI leve 11 milliards de dollars</title>
  <link>https://example.com/openai</link>
  <pubDate>{recent}</pubDate>
  <description>&lt;p&gt;Une tour record&lt;/p&gt;</description>
</item>
<item>
  <title>Vieille actualite hors periode</title>
  <link>https://example.com/old</link>
  <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate>
  <description>Hors periode</description>
</item>
</channel></rss>
"""


class TestParseRss(unittest.TestCase):
    def test_keeps_recent_items_and_strips_html(self):
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        since = datetime.now(timezone.utc) - timedelta(days=7)
        xml = SAMPLE_RSS.format(recent=recent.strftime("%a, %d %b %Y %H:%M:%S GMT"))
        items = parse_rss(xml, "Test", since)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["title"].startswith("OpenAI"))
        self.assertEqual(items[0]["summary"], "Une tour record")
        self.assertEqual(items[0]["source"], "Test")


class TestValidatePosts(unittest.TestCase):
    def test_accepts_valid_thread(self):
        posts = ["Post " + str(i) for i in range(THREAD_POSTS)]
        self.assertEqual(validate_posts(posts), posts)

    def test_rejects_wrong_count(self):
        with self.assertRaises(ValueError):
            validate_posts(["un", "deux"])

    def test_rejects_too_long_post(self):
        posts = ["x" * 281] + ["ok"] * (THREAD_POSTS - 1)
        with self.assertRaises(ValueError):
            validate_posts(posts)

    def test_rejects_empty_post(self):
        posts = ["ok"] * (THREAD_POSTS - 1) + ["  "]
        with self.assertRaises(ValueError):
            validate_posts(posts)


class TestNotionBlocks(unittest.TestCase):
    def test_headings_bullets_and_links(self):
        blocks = markdown_to_notion_blocks(
            "## Titre\n\nParagraphe avec [lien](https://example.com) et **gras**.\n\n- element"
        )
        self.assertEqual(
            [b["type"] for b in blocks], ["heading_2", "paragraph", "bulleted_list_item"]
        )
        rich = blocks[1]["paragraph"]["rich_text"]
        self.assertTrue(any(part["text"].get("link") for part in rich))
        self.assertTrue(any(part.get("annotations", {}).get("bold") for part in rich))


class TestBluesky(unittest.TestCase):
    def test_facet_covers_link_bytes(self):
        text = "A lire https://example.com/briefing suite"
        facet = facet_for_link(text, "https://example.com/briefing")
        self.assertEqual(facet["features"][0]["uri"], "https://example.com/briefing")
        self.assertGreater(facet["index"]["byteEnd"], facet["index"]["byteStart"])

    def test_facet_raises_when_missing(self):
        with self.assertRaises(ValueError):
            facet_for_link("sans lien", "https://example.com")

    def test_post_url(self):
        self.assertEqual(
            bluesky_post_url("moi.bsky.social", "at://did:plc:x/app.bsky.feed.post/abc123"),
            "https://bsky.app/profile/moi.bsky.social/post/abc123",
        )


if __name__ == "__main__":
    unittest.main()
