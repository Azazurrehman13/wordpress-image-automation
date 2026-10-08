# WordPress Product Image Automation (local)

A local tool that finds an existing WooCommerce product, reads the **original image source URL** from its
description, collects the real product images from that **source**, lets Claude pick the best 4–5 and the single
best main image, processes them with Pillow/OpenCV, and uploads them to the **destination** WordPress site you log in to.

```
React (Vite)  ->  FastAPI  ->  Playwright + WP/WooCommerce REST  ->  destination site
                         \->  source website (httpx, Playwright fallback)
                         \->  Claude API (backend only)   \->  SQLite (data/app.db)
```

## Requirements (Windows)
* Python 3.11+ and Node.js 18+
* An Anthropic API key
* A WordPress admin account with WooCommerce

## Setup
```bat
scripts\setup.bat
```
Then open `backend\.env` and set `ANTHROPIC_API_KEY=...` (this file is git-ignored; the key never reaches the browser).

## Run
```bat
scripts\start.bat
```
Opens http://localhost:5173. The API runs on http://127.0.0.1:8000.
Start the backend with `python run.py` (not `uvicorn --reload`): Playwright needs the Windows Proactor event loop.

## How a run works
1. **Connect** – enter the login URL (`https://site.com/wp-login.php`), username, password. Playwright logs in with a
   persistent profile in `data/browser_profile/` (reused next time). Headless can be switched off to watch it or to finish 2FA.
2. **Find product** – WooCommerce REST → WP REST → browser search. Ambiguous or low-confidence results need your pick.
3. **Source URL** – the description (REST + the real product page) is parsed. Social/legal/self links are ignored; remaining
   links are scored (labels like "original image", product-style paths, name similarity). If none qualifies you get
   "Source URL not found" and can paste one manually. Nothing is searched on the open internet.
4. **Images** – `img/src/srcset/data-*`, OpenGraph, JSON-LD, gallery links and script data are scanned; thumbnails are upgraded to
   full size; downloads are validated (type, size, dimensions, corruption, logos/banners) and de-duplicated (aHash+dHash+pHash).
5. **Claude** – scores every candidate (Pydantic-validated JSON), picks 4–5 views with visual coverage, then ONE best image.
6. **Processing** – crop (Claude's box is only used if it passes checks), luminance-only adjustment, resize, compress, quality checks.
7. **Metadata** – title, alt, caption, description from what is visible; URLs and source domains are stripped.
8. **Upload & review** – images go to the media library. The product is **not** changed until you approve.
9. **Publish** – the first image becomes the Product Image, the others the Gallery in AI order. Then verification runs.

## Design decisions to know about
* **Nothing live changes before approval.** Media is uploaded early (harmless); the product is only updated when you publish.
  Cancel deletes the uploaded media again. New listings are created as a draft only at publish time.
* **Only image fields are written** (plus the description *only* to remove the source link, controlled by
  `STRIP_SOURCE_FROM_DESCRIPTION`). Price, SKU, stock, categories, attributes, shipping etc. are never sent.
* **REST for writes.** After the Playwright login, the session cookie + REST nonce are used; this is far more reliable than driving wp-admin.
* **Status handling.** Drafts/pending products are published; already-published (or private) products keep their status.
* **Auto Publish** is OFF by default and also stays off when the run produced warnings.
* **Only one job runs at a time** (one browser session).

## Configuration (`backend/.env`)
See `.env.example`. Notable: `CLAUDE_MODEL`, `MIN_IMAGE_SIDE`, `MAX_UPLOAD_BYTES`, `OUTPUT_FORMAT=jpeg|webp`,
`ALLOW_PRIVATE_SOURCE_URLS` (keep `false`; only enable to test against a local source).

## Security
* Claude key lives only in the backend; the CORS list is limited to the local UI origin.
* Password is held in memory only and never persisted/logged (log lines are redacted). **Disconnect** deletes the browser profile.
* Source URLs/redirects are validated (http(s) only, no credentials, private/loopback addresses blocked); downloads are size-limited and verified.
* File serving only resolves images registered in the database under `data/jobs/`; filenames are sanitised.

## Tests
```bat
scripts\test.bat
```
Covers matching, URL extraction/identification, image extraction, validation, de-duplication, crop, Claude schema
validation, selection logic, gallery ordering, upload, security, and a full fake end-to-end pipeline.
`backend/tests/integration/test_wordpress_staging.py` runs only if `WP_STAGING_*` variables are set —
**point it at a staging site, never production.**

## Limits worth knowing
* Sources behind Cloudflare/bot protection may refuse access ("source unavailable"). Use headless OFF and a manual URL, or another source.
* Make sure you have the right to reuse the images you collect.
* A plugin that disables the REST API or changes WooCommerce auth can block uploads; the error is shown with its HTTP status.
