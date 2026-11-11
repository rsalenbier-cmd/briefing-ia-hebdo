"""Aides pures du briefing IA : parsing RSS, validation du fil, conversion Notion.

Aucune entrée-sortie ici : ces fonctions sont testables sans réseau ni SDK.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

MAX_POST_CHARS = 280
THREAD_POSTS = 5


def parse_rss(xml_text: str, source: str, since: datetime) -> list[dict[str, str]]:
    """Extrait les items RSS publiés après `since`. Pure : aucun réseau."""
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        summary = (item.findtext("description") or "").strip()
        if not title or not link:
            continue
        published_dt = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                published_dt = datetime.strptime(published, fmt)
                break
            except ValueError:
                continue
        if published_dt is not None:
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=since.tzinfo)
            if published_dt < since:
                continue
        summary = re.sub(r"<[^>]+>", "", summary)[:400]
        items.append({"title": title, "link": link, "source": source, "published": published, "summary": summary})
    return items


def validate_posts(posts: list[str]) -> list[str]:
    """Vérifie le fil : THREAD_POSTS posts, 280 caractères max chacun."""
    if len(posts) != THREAD_POSTS:
        raise ValueError(f"Le fil doit contenir {THREAD_POSTS} posts, reçu : {len(posts)}")
    for index, post in enumerate(posts, start=1):
        if not post.strip():
            raise ValueError(f"Le post {index} est vide")
        if len(post) > MAX_POST_CHARS:
            raise ValueError(f"Le post {index} dépasse {MAX_POST_CHARS} caractères ({len(post)})")
    return posts


def _md_inline_to_rich(text: str) -> list[dict[str, Any]]:
    """Convertit un fragment markdown (gras **x**, liens [t](u)) en rich_text Notion."""
    rich: list[dict[str, Any]] = []
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)|\*\*([^*]+)\*\*")
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            rich.append({"type": "text", "text": {"content": text[cursor:match.start()]}})
        if match.group(1):
            rich.append({"type": "text", "text": {"content": match.group(1), "link": {"url": match.group(2)}}})
        elif match.group(3):
            rich.append({"type": "text", "text": {"content": match.group(3)}, "annotations": {"bold": True}})
        cursor = match.end()
    if cursor < len(text) or not rich:
        rich.append({"type": "text", "text": {"content": text[cursor:]}})
    return rich


def markdown_to_notion_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convertit le markdown du briefing en blocs Notion. Pure : aucun réseau."""
    blocks: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": _md_inline_to_rich(stripped[4:])}})
        elif stripped.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _md_inline_to_rich(stripped[3:])}})
        elif stripped.startswith("- "):
            blocks.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _md_inline_to_rich(stripped[2:])}})
        else:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _md_inline_to_rich(stripped)}})
    return blocks


def _utf8_range(text: str, start: int, end: int) -> dict[str, int]:
    """Positions d'octets UTF-8 d'une sous-chaîne (facettes Bluesky)."""
    byte_start = len(text[:start].encode("utf-8"))
    return {"byteStart": byte_start, "byteEnd": byte_start + len(text[start:end].encode("utf-8"))}


def facet_for_link(text: str, link: str) -> dict[str, Any]:
    """Facette Bluesky rendant `link` cliquable dans `text`. Lève si absent."""
    start = text.find(link)
    if start < 0:
        raise ValueError(f"Lien introuvable dans le texte : {link}")
    return {
        "index": _utf8_range(text, start, start + len(link)),
        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}],
    }


def bluesky_post_url(handle: str, uri: str) -> str:
    """URL web d'un post Bluesky à partir de son AT URI."""
    return f"https://bsky.app/profile/{handle}/post/{uri.split('/')[-1]}"
