# Briefing IA hebdo

Workflow Mistral qui génère chaque semaine le briefing IA de Régis Salenbier :
collecte d'actualités via flux RSS, synthèse par un modèle Mistral, création d'une page
Notion et publication d'un fil Bluesky (5 posts, 280 caractères max).

## Workflow `briefing-ia-hebdo`

```
RSS -> collect_news -> draft_briefing -> create_notion_page -> post_bluesky_thread
```

- `collect_news` : items RSS des 7 derniers jours (TechCrunch, VentureBeat, The Verge, Ars Technica).
- `draft_briefing` : synthèse JSON (thème, markdown Notion, fil de 5 posts de 280 caractères max).
- `create_notion_page` : page Notion sous la page parente « Briefings IA ».
- `post_bluesky_thread` : fil Bluesky enfilé, le dernier post pointe vers la page Notion.

Entrée : `{ "dry_run": true|false }` — en `dry_run`, le briefing est rédigé mais rien
n'est publié (utile pour tester).

## Secrets du déploiement (Secrets Manager du workspace)

| Variable d'environnement | Secret | Rôle |
| --- | --- | --- |
| `BSKY_HANDLE` | bsky_handle | Handle Bluesky (ex. `moi.bsky.social`) |
| `BSKY_APP_PASSWORD` | bsky_app_password | Mot de passe d'application Bluesky |
| `NOTION_TOKEN` | notion_token | Jeton d'intégration interne Notion |
| `NOTION_PARENT_PAGE_ID` | notion_parent_page_id | ID de la page parente « Briefings IA » |

## Planning

Horaire : chaque dimanche à 21h Europe/Paris (calendrier : day_of_week=0, hour=20 UTC).

## Tests

```bash
python -m unittest discover -s apps/worker/tests -p "test_briefing*"
```

Les fonctions pures (parsing RSS, validation du fil, conversion Notion, facettes Bluesky)
sont dans `apps/worker/src/worker/workflows/briefing_utils.py` et testées sans réseau.
