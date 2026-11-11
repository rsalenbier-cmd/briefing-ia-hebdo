"""Briefing IA hebdo : collecte d'actualités, synthèse, page Notion et fil Bluesky.

Workflow récurrent de Régis Salenbier : chaque dimanche soir, il collecte les actualités
de l'industrie de l'IA via des flux RSS, les synthétise avec un modèle Mistral, crée
une page Notion et publie un fil Bluesky de 5 posts (280 caractères max par post).

Secrets d'exécution (bornés au déploiement) :
- BSKY_HANDLE, BSKY_APP_PASSWORD : compte Bluesky (mot de passe d'application).
- NOTION_TOKEN, NOTION_PARENT_PAGE_ID : intégration Notion et page parente
  « Briefings IA », partagée avec l'intégration et publiée sur le web.
"""

import asyncio
import json
import os
import urllib.error
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.request import Request, urlopen

import mistralai.workflows as workflows
from pydantic import BaseModel, Field

from worker.workflows.briefing_utils import (
    MAX_POST_CHARS,
    THREAD_POSTS,
    bluesky_post_url,
    facet_for_link,
    markdown_to_notion_blocks,
    parse_rss,
    validate_posts,
)

# --- Sources RSS de l'actualité IA (nom affiché, URL du flux) ---

RSS_FEEDS: list[tuple[str, str]] = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/"),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/rss.xml"),
    ("Ars Technica", "https://arstechnica.com/ai/feed/"),
]

BLUESKY_API = "https://bsky.social/xrpc"
NOTION_API = "https://api.notion.com/v1"


class BriefingInput(BaseModel):
    dry_run: bool = False


class BriefingResult(BaseModel):
    theme: str
    notion_url: str | None = None
    post_urls: list[str] = Field(default_factory=list)
    dry_run: bool = False


# --- Aides d'entrée/sortie (utilisées uniquement dans des activités) ---


def _http_get(url: str) -> bytes:
    """Télécharge une URL (bloquant, à appeler dans un thread)."""
    request = Request(url, headers={"User-Agent": "briefing-ia-hebdo/1.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL constantes du projet
        return response.read()


def _http_post(url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """POST HTTP (bloquant, à appeler dans un thread). Retourne (statut, corps)."""
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=90) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _json_post(url: str, body: dict[str, Any], token: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, raw = _http_post(url, json.dumps(body).encode("utf-8"), headers)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = {"raw": raw[:500].decode("utf-8", errors="replace")}
    return status, parsed


# --- Activités (toutes les entrées/sorties ici, et seulement ici) ---


@workflows.activity()
async def collect_news() -> list[dict[str, str]]:
    """Collecte les items RSS des 7 derniers jours depuis les sources du projet."""
    since = datetime.now(tz=UTC) - timedelta(days=7)

    async def fetch_one(source: str, url: str) -> list[dict[str, str]]:
        try:
            raw = await asyncio.to_thread(_http_get, url)
            return parse_rss(raw.decode("utf-8", errors="replace"), source, since)
        except (OSError, ValueError):
            return []

    gathered = await asyncio.gather(*(fetch_one(source, url) for source, url in RSS_FEEDS))
    items = [item for batch in gathered for item in batch]
    if not items:
        raise ValueError("Aucune actualité collectée : tous les flux RSS ont échoué ou sont vides")
    return items


@workflows.activity()
async def draft_briefing(news: list[dict[str, str]]) -> dict[str, Any]:
    """Synthétise le briefing avec un modèle Mistral et renvoie un brouillon validé."""
    from utils.mistral import mistral_client

    client = mistral_client()
    catalog = "\n".join(
        f"- [{item['source']}] {item['title']} ({item['link']}) : {item['summary']}" for item in news[:120]
    )
    prompt = f"""Tu prépares le briefing hebdomadaire IA de Régis Salenbier, en français.
Actualités des 7 derniers jours :
{catalog}

Produis un objet JSON STRICT avec :
- "theme" : le thème dominant de la semaine (1 phrase).
- "notion_title" : titre de la page Notion, format « Briefing IA — semaine du ... au ... ».
- "notion_markdown" : briefing complet en markdown (titres ## et ###, listes « - », gras **x**, liens [texte](url)), couvrant : thème, 🧠 modèles et produits, 💰 financements et partenariats, 🏛️ politique et régulation, 📌 pourquoi cette semaine compte.
- "posts" : un fil Bluesky de {THREAD_POSTS} posts en français, chacun {MAX_POST_CHARS} caractères MAXIMUM, dans ce style : accroche punchy, paragraphes courts, question rhétorique, ton direct et factuel, emojis. Le post 1 = accroche sur le thème ; post 2 = fait le plus notable côté modèles ; post 3 = annonces financières (montants) ; post 4 = politique/régulation ; post 5 = appel à lire, se terminant par la mention exacte « LIEN_NOTION » sur sa propre ligne, puis des hashtags #IA. Ne dépasse JAMAIS {MAX_POST_CHARS} caractères par post, compte-les. La chaîne « LIEN_NOTION » ne doit apparaître QUE dans le post 5.
Réponds uniquement par l'objet JSON."""

    response = client.chat.complete(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    draft = json.loads(content)
    posts = validate_posts([str(post) for post in draft.get("posts", [])])
    if "LIEN_NOTION" not in posts[-1]:
        raise ValueError("Le dernier post doit contenir la mention LIEN_NOTION")
    return {
        "theme": str(draft.get("theme", "")),
        "notion_title": str(draft.get("notion_title", "")),
        "notion_markdown": str(draft.get("notion_markdown", "")),
        "posts": posts,
    }


@workflows.activity()
async def create_notion_page(title: str, markdown: str) -> str:
    """Crée la page Notion sous la page parente et renvoie son URL."""
    token = os.environ.get("NOTION_TOKEN")
    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not token or not parent_id:
        raise ValueError("NOTION_TOKEN ou NOTION_PARENT_PAGE_ID manquant dans l'environnement")
    body = {
        "parent": {"page_id": parent_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
        "children": markdown_to_notion_blocks(markdown)[:100],
    }
    status, page = await asyncio.to_thread(_json_post, f"{NOTION_API}/pages", body, token)
    if status >= 400:
        raise ValueError(f"Notion a refusé la création de page ({status}) : {json.dumps(page)[:300]}")
    return str(page.get("url", ""))


@workflows.activity()
async def post_bluesky_thread(posts: list[str], notion_url: str) -> list[str]:
    """Publie le fil Bluesky et renvoie les URLs web des posts."""
    handle = os.environ.get("BSKY_HANDLE")
    password = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not password:
        raise ValueError("BSKY_HANDLE ou BSKY_APP_PASSWORD manquant dans l'environnement")

    status, session = await asyncio.to_thread(
        _json_post,
        f"{BLUESKY_API}/com.atproto.server.createSession",
        {"identifier": handle, "password": password},
    )
    if status >= 400:
        raise ValueError(f"Connexion Bluesky refusée ({status}) : {json.dumps(session)[:300]}")
    did, access = str(session.get("did", "")), str(session.get("accessJwt", ""))
    auth_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access}"}

    root: dict[str, str] | None = None
    parent: dict[str, str] | None = None
    uris: list[str] = []
    for index, text in enumerate(posts):
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "langs": ["fr"],
        }
        if index == len(posts) - 1:
            final_text = text.replace("LIEN_NOTION", notion_url)
            record["text"] = final_text
            record["facets"] = [facet_for_link(final_text, notion_url)]
            if len(final_text) > MAX_POST_CHARS:
                raise ValueError(f"Le post final dépasse {MAX_POST_CHARS} caractères ({len(final_text)})")
        if parent and root:
            record["reply"] = {
                "root": {"did": root["did"], "uri": root["uri"], "cid": root["cid"]},
                "parent": {"did": parent["did"], "uri": parent["uri"], "cid": parent["cid"]},
            }
        status, created = await asyncio.to_thread(
            _json_post,
            f"{BLUESKY_API}/com.atproto.repo.createRecord",
            {"repo": did, "collection": "app.bsky.feed.post", "record": record},
        )
        if status >= 400:
            raise ValueError(f"Publication Bluesky refusée ({status}) : {json.dumps(created)[:300]}")
        post_ref = {"did": did, "uri": str(created.get("uri", "")), "cid": str(created.get("cid", ""))}
        uris.append(post_ref["uri"])
        root = root or post_ref
        parent = post_ref
    return [bluesky_post_url(handle, uri) for uri in uris]


# --- Workflow ---


@workflows.workflow.define(
    name="briefing-ia-hebdo",
    workflow_display_name="Briefing IA hebdo",
    workflow_description="Briefing hebdomadaire de l'industrie IA : synthèse, page Notion et fil Bluesky.",
)
class BriefingWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: BriefingInput) -> BriefingResult:
        news = await collect_news()
        draft = await draft_briefing(news)
        if input.dry_run:
            return BriefingResult(theme=draft["theme"], dry_run=True)
        notion_url = await create_notion_page(draft["notion_title"], draft["notion_markdown"])
        post_urls = await post_bluesky_thread(draft["posts"], notion_url)
        return BriefingResult(theme=draft["theme"], notion_url=notion_url, post_urls=post_urls)
