"""Finds product image URLs on the ORIGINAL SOURCE page (not on the destination site).

Shopify-style shops (/products/<handle>) are read from the shop's own product data first: that list is exactly the
product's own gallery, so nothing from "related products" can get in, and no browser is needed.
Otherwise the static HTML of most shops contains only the first image or two; the rest of the gallery is loaded by JavaScript.
So the source page is also opened in a real browser, the gallery is clicked through, and everything found
(static HTML + rendered DOM + network traffic) is merged."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.config import cfg
from app.services.playwright_service import PlaywrightService
from app.utils.errors import AppError
from app.utils.http import safe_fetch
from app.utils.security import validate_public_url

IMG_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)
JUNK = ("logo", "icon", "sprite", "favicon", "avatar", "placeholder", "pixel", "tracking", "spinner",
        "loader", "badge", "payment", "gravatar", "blank", "banner", "social", "flag-", "1x1", "arrow", "star-rating")
LAZY_ATTRS = ("data-large_image", "data-full-image", "data-zoom-image", "data-large", "data-original",
              "data-src", "data-lazy-src", "data-lazy", "data-image", "src")
SCRIPT_IMG = re.compile(r"https?:\\?/\\?/[^\"'\s<>\\]*?(?:\\/[^\"'\s<>\\]*?)*\.(?:jpe?g|png|webp)", re.I)
SIZE_PARAMS = {"w", "h", "width", "height", "sw", "sh", "resize", "fit", "crop", "dpr"}
BOT_WALL = ("access denied", "just a moment", "verify you are human", "pardon our interruption",
            "attention required", "cf-challenge", "are you a robot")
NEXT_SELECTORS = ["button[aria-label*='next' i]", ".swiper-button-next", ".slick-next", ".flickity-button.next",
                  "[class*='carousel'] [class*='next']", "button[class*='next']", "[class*='gallery'] [class*='next']"]
THUMB_SELECTORS = [".woocommerce-product-gallery__image", "[class*='thumb'] img", "[class*='thumbnail'] button",
                   "[class*='thumb'] button", "[data-thumb]", "[class*='gallery'] [role='tab']"]
# connection-level failures: retrying in another browser mode cannot fix them
NETWORK_DOWN = ("ERR_CONNECTION_TIMED_OUT", "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_ADDRESS_UNREACHABLE")
DOM_IMAGES_JS = "() => [...document.images].filter(i => (i.naturalWidth || 0) >= 300).map(i => i.currentSrc || i.src)"


# Headings that start the "other products" part of a product page. Everything after them is not this product's gallery.
_RELATED_HEADING = re.compile(
    r"<h[1-6][^>]*>(?:\s|<[^>]+>)*(?:similar items|you may also like|you might also like|recently viewed|related products?|"
    r"complete the look|shop the look|more from|customers also|recommended for you)", re.I)
_SWATCH = re.compile(r"_(?:small|swatch|pico|icon)\b", re.I)


def _cut_related(html: str) -> str:
    m = _RELATED_HEADING.search(html or "")
    return html[: m.start()] if m else (html or "")


def _stem(url: str) -> str:
    """File name without extension, size suffix (_1200x, _800x, _small) and trailing UUID: 'W1740L694BLKF' for
    'W1740L694BLKF_7794985b-ac24-463c-aa1f-71a1418a25bc_1200x.jpg'. Images of one product usually share this stem."""
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    name = re.sub(r"\.(?:jpe?g|png|webp)$", "", name, flags=re.I)
    name = re.sub(r"_(?:\d{2,4}x\d{0,4}(?:_crop_\w+)?|small|medium|large|grande|compact|pico|icon|thumb)$", "", name, flags=re.I)
    name = re.sub(r"[_-][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "", name, flags=re.I)
    return name


def _og_image(html: str, base: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    for prop in ("og:image:secure_url", "og:image"):
        m = soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            return urljoin(base, m["content"].strip())
    return None


def sibling_gallery(cands: list["ImageCandidate"], htmls: list[str], base: str) -> list["ImageCandidate"]:
    """The product's own gallery, found WITHOUT guessing: the page's main image (og:image) names the product
    (e.g. W1740L694BLKF); its other photos carry the same name with another view letter (…BLKR, …BLKD). Images of
    'similar items' (W1881L694…) never match, so other products cannot get in. Returns [] when this does not apply."""
    og = next((u for u in (_og_image(h, base) for h in htmls) if u), None)
    if not og:
        return []
    og_stem = _stem(og)
    og_dir = urlsplit(og).path.rsplit("/", 1)[0]
    if len(og_stem) < 6:
        return []
    picked: dict[str, ImageCandidate] = {}
    for c in cands:
        if urlsplit(c.url).path.rsplit("/", 1)[0] != og_dir:
            continue
        st = _stem(c.url)
        n = 0
        for a, b in zip(og_stem, st):
            if a != b:
                break
            n += 1
        if n < 6 or n < len(og_stem) - 1:
            continue
        if c.fallbacks and all(_SWATCH.search(f) for f in c.fallbacks):  # colour swatch, not a gallery photo
            continue
        cur = picked.get(st)
        if cur is None or (cur.url.startswith("http://") and c.url.startswith("https://")):  # prefer the https copy of the same image
            picked[st] = c

    def pos(st: str) -> int:
        hits = [h.find(st) for h in htmls if h.find(st) != -1]
        return min(hits) if hits else 10**9

    return [picked[s] for s in sorted(picked, key=lambda s: (s != og_stem, pos(s)))]


@dataclass
class ImageCandidate:
    url: str
    fallbacks: list[str] = field(default_factory=list)
    origin: str = "img"
    priority: int = 0


@dataclass
class ExtractionResult:
    final_url: str
    candidates: list[ImageCandidate]
    used_browser: bool = False
    browser_error: str | None = None  # set when the browser step failed but the static page still gave images
    method: str = ""  # how the images were found: shop product data / browser / plain page (shown in the job steps)
    exact: bool = False  # True when the list is the product's own gallery from the shop's product data (no other products mixed in)


def _strip_size_query(url: str) -> str:
    p = urlsplit(url)
    if not p.query or not re.search(r"\.(?:jpe?g|png|webp)$", p.path, re.I):
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in SIZE_PARAMS]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def upgrade_url(url: str) -> str:
    """Turn a thumbnail URL into the full-size original (WordPress -300x300, Shopify _300x, ?width=400 style params).
    The original URL is always kept as a fallback, so a signed URL that cannot be changed still works."""
    u = re.sub(r"-\d{2,4}x\d{2,4}(?=\.(?:jpe?g|png|webp)(?:\?|$))", "", url, flags=re.I)
    u = re.sub(r"_(?:\d{2,4}x\d{0,4}|pico|icon|thumb|small|compact|medium|large|grande)(?=\.(?:jpe?g|png|webp)(?:\?|$))", "", u, flags=re.I)
    return _strip_size_query(u)


def _best_srcset(srcset: str) -> str | None:
    best, best_w = None, -1.0
    for part in re.split(r",\s+(?=\S)", srcset.strip()):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0.0
        if len(bits) > 1:
            try:
                w = float(bits[1].rstrip("wx"))
            except ValueError:
                w = 0.0
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def _is_junk(url: str) -> bool:
    name = urlsplit(url).path.rsplit("/", 1)[-1].lower()
    return any(j in name for j in JUNK)


def parse_html(html: str, base_url: str) -> list[ImageCandidate]:
    soup = BeautifulSoup(_cut_related(html), "html.parser")
    found: list[tuple[str, str, int]] = []  # (url, origin, priority)

    def add(u: str | None, origin: str, prio: int) -> None:
        if not u or u.startswith("data:"):
            return
        full = urljoin(base_url, u.strip())
        if urlsplit(full).scheme in ("http", "https"):
            found.append((full, origin, prio))

    for gallery in soup.select(".woocommerce-product-gallery, [class*=product-gallery], [class*=product__media]"):
        for el in gallery.find_all(["img", "a", "div", "source"]):
            for attr in LAZY_ATTRS:
                if el.get(attr):
                    add(el.get(attr), "gallery", 100)
            for attr in ("srcset", "data-srcset"):
                if el.get(attr):
                    add(_best_srcset(el[attr]), "gallery", 100)
            if el.name == "a" and el.get("href") and IMG_EXT.search(el["href"]):
                add(el["href"], "gallery-link", 110)
    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        for m in soup.find_all("meta", attrs={"property": prop}) + soup.find_all("meta", attrs={"name": prop}):
            add(m.get("content"), "meta", 80)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.get("@graph", []) if isinstance(node.get("@graph"), list) else [])
                img = node.get("image")
                for i in (img if isinstance(img, list) else [img]):
                    add(i.get("url") if isinstance(i, dict) else i, "json-ld", 90)
            elif isinstance(node, list):
                stack.extend(node)
    for img in soup.find_all("img"):
        for attr in LAZY_ATTRS:
            if img.get(attr):
                add(img.get(attr), "img", 50)
        for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
            if img.get(attr):
                add(_best_srcset(img[attr]), "srcset", 60)
    for src in soup.find_all("source"):  # <picture><source srcset=...> : the high-resolution versions live here
        for attr in ("srcset", "data-srcset"):
            if src.get(attr):
                add(_best_srcset(src[attr]), "srcset", 60)
    for a in soup.find_all("a", href=True):
        if IMG_EXT.search(a["href"]):
            add(a["href"], "link", 70)
    for s in soup.find_all("script"):
        txt = s.string or ""
        if len(txt) < 3_000_000:
            for m in SCRIPT_IMG.findall(txt):
                add(m.replace("\\/", "/"), "script", 20)

    out: dict[str, ImageCandidate] = {}
    for url, origin, prio in found:
        if _is_junk(url):
            continue
        up = upgrade_url(url)
        cand = out.get(up)
        if cand is None:
            out[up] = ImageCandidate(url=up, fallbacks=[url] if url != up else [], origin=origin, priority=prio)
        else:
            if url != up and url not in cand.fallbacks:
                cand.fallbacks.append(url)
            cand.priority = max(cand.priority, prio)
    return sorted(out.values(), key=lambda c: c.priority, reverse=True)


def merge_candidates(*groups: list[ImageCandidate]) -> list[ImageCandidate]:
    merged: dict[str, ImageCandidate] = {}
    for group in groups:
        for c in group:
            cur = merged.get(c.url)
            if cur is None:
                merged[c.url] = ImageCandidate(c.url, list(c.fallbacks), c.origin, c.priority)
            else:
                cur.priority = max(cur.priority, c.priority)
                cur.fallbacks.extend(f for f in c.fallbacks if f not in cur.fallbacks and f != cur.url)
    return sorted(merged.values(), key=lambda c: c.priority, reverse=True)


def _short(e: BaseException) -> str:
    line = (str(e).strip().splitlines() or [type(e).__name__])[0]
    return line[:220]


class ImageExtractor:
    def __init__(self, pw: PlaywrightService) -> None:
        self.pw = pw

    # ------------------------------------------------------- shop product data
    @staticmethod
    def _shopify_json_url(url: str) -> str | None:
        """https://shop.com/products/<handle>  ->  https://shop.com/products/<handle>.json (Shopify's own product data)."""
        p = urlsplit(url)
        m = re.match(r"^(.*?/products/[^/?#]+)", p.path)
        if not m:
            return None
        path = re.sub(r"\.(?:json|js)$", "", m.group(1))
        return urlunsplit((p.scheme, p.netloc, path + ".json", "", ""))

    async def _shopify_gallery(self, url: str) -> list[ImageCandidate]:
        """The product's own gallery, in the shop's order. Empty when the site is not Shopify or hides the data."""
        api = self._shopify_json_url(url)
        if not api:
            return []
        try:
            r = await safe_fetch(api, max_bytes=2_000_000, accept="application/json,*/*;q=0.5")
            if r.status != 200:
                return []
            data = json.loads(r.content.decode("utf-8", "ignore"))
        except (AppError, ValueError):
            return []
        product = data.get("product") if isinstance(data, dict) else None
        images = (product or {}).get("images") or []
        images = sorted((i for i in images if isinstance(i, dict)), key=lambda i: i.get("position") or 0)
        out: list[ImageCandidate] = []
        seen: set[str] = set()
        for n, im in enumerate(images):
            src = im.get("src")
            if src and src not in seen:
                seen.add(src)
                out.append(ImageCandidate(url=src, origin="shopify", priority=1000 - n))
        return out

    async def extract(self, url: str) -> ExtractionResult:
        url = await validate_public_url(url)

        exact = await self._shopify_gallery(url)
        if exact:
            return ExtractionResult(url, exact, used_browser=False, exact=True, method="shop product data (exact gallery)")

        limit = cfg.max_candidates * 2
        final_url = url
        static: list[ImageCandidate] = []
        static_html = ""
        rendered_html = ""
        static_err = ""
        try:
            r = await safe_fetch(url, max_bytes=6_000_000, accept="text/html,application/xhtml+xml,image/*")
            final_url = r.url
            if r.content_type.startswith("image/") or IMG_EXT.search(urlsplit(r.url).path):
                return ExtractionResult(r.url, [ImageCandidate(url=r.url, origin="direct", priority=100)])
            if r.status == 200:
                static_html = r.content.decode("utf-8", "ignore")
                static = parse_html(static_html, r.url)
            else:
                static_err = f"the server answered HTTP {r.status} to a plain request"
        except AppError as e:
            if e.code != "source_unavailable":
                raise
            static_err = e.message

        rendered: list[ImageCandidate] = []
        browser_error: str | None = None
        used_browser = False
        try:
            html, net, dom, final = await self._render(final_url)
            used_browser, final_url = True, final
            rendered_html = html
            rendered = (
                parse_html(html, final)
                + [ImageCandidate(url=upgrade_url(u), fallbacks=[u] if upgrade_url(u) != u else [], origin="dom", priority=55)
                   for u in dom if not _is_junk(u)]
                + [ImageCandidate(url=upgrade_url(u), fallbacks=[u] if upgrade_url(u) != u else [], origin="network", priority=40)
                   for u in net if not _is_junk(u)]
            )
        except AppError as e:
            if e.code == "source_url_invalid":
                raise
            browser_error = e.message

        merged = merge_candidates(rendered, static)
        if not merged:
            raise AppError("source_unavailable", browser_error or static_err or "No images were found on the page.", 502)
        gallery = sibling_gallery(merged, [h for h in (rendered_html, static_html) if h], final_url)
        if len(gallery) >= 2:  # the product's own photos, identified by the main image's name: nothing from other products
            return ExtractionResult(final_url, gallery[:10], used_browser, browser_error,
                                    method="product gallery (same-name images)", exact=True)
        if gallery:  # only the main image is recognisable: put it first, the other-product checks still apply
            merged = gallery + [c for c in merged if c.url != gallery[0].url]
        return ExtractionResult(final_url, merged[:limit], used_browser, browser_error,
                                method="browser + page scan" if used_browser else "plain page scan only")

    # ----------------------------------------------------------------- browser
    async def _render(self, url: str):
        """Open the page in a real browser. Retries with HTTP/1.1, then with a visible window, because many shops block
        headless Chromium (HTTP 403, ERR_HTTP2_PROTOCOL_ERROR, bot-protection pages)."""
        attempts = [(True, False, "headless"), (True, True, "headless, HTTP/1.1")]
        if os.environ.get("SOURCE_BROWSER_HEADED_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}:
            attempts.append((False, False, "visible window"))
        last = ""
        tried: list[str] = []
        for headless, no_h2, label in attempts:
            tried.append(label)
            try:
                return await self._render_once(url, headless, no_h2)
            except AppError as e:
                if e.code == "source_url_invalid":
                    raise
                last = e.message
            except Exception as e:  # noqa: BLE001  (Playwright errors are all named "Error": keep the real message)
                last = _short(e)
            if any(k in last.upper() for k in NETWORK_DOWN):
                break  # this computer cannot reach the site at all: other browser modes will fail the same way, do not waste minutes
        raise AppError("source_unavailable", f"The original source could not be opened: {last} "
                       f"(tried: {', '.join(tried)}). The site may block automated browsers or be unreachable from this network.", 502)

    async def _render_once(self, url: str, headless: bool, disable_http2: bool):
        net: list[str] = []
        async with self.pw.ephemeral_context(headless=headless, disable_http2=disable_http2) as ctx:
            page = await ctx.new_page()

            def on_response(resp) -> None:
                try:
                    if resp.request.resource_type == "image" and resp.status == 200:
                        size = int(resp.headers.get("content-length", "0") or 0)
                        if size == 0 or size >= 15_000:
                            net.append(resp.url)
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:  # noqa: BLE001
                if not any(k in str(e) for k in ("ERR_ABORTED", "frame was detached")):
                    raise
                # A redirect (country/language switch, www or trailing-slash redirect) interrupted the first navigation:
                # let the page settle and carry on from wherever it landed instead of giving up.
                resp = None
                await page.wait_for_timeout(2_500)
                if page.url.startswith("about:"):
                    raise
            if resp is not None and resp.status >= 400:
                raise AppError("source_unavailable", f"The original source returned HTTP {resp.status}.", 502)
            await validate_public_url(page.url)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:  # noqa: BLE001
                pass
            await self._explore_gallery(page)
            html = await page.content()
            dom = await page.evaluate(DOM_IMAGES_JS)
            if len(html) < 20_000 and any(w in html[:8000].lower() for w in BOT_WALL):
                raise AppError("source_unavailable", "The source showed a bot-protection page instead of the product.", 502)
            return html, net, dom, page.url

    @staticmethod
    async def _explore_gallery(page) -> None:
        """Scroll and click through the product gallery so lazy-loaded slides load their full-size images. Never fatal."""
        try:
            for _ in range(6):
                await page.mouse.wheel(0, 1400)
                await page.wait_for_timeout(250)
            await page.evaluate("window.scrollTo(0, 0)")
            for sel in NEXT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() and await btn.is_visible():
                        for _ in range(10):
                            await btn.click(timeout=1_500)
                            await page.wait_for_timeout(350)
                        break
                except Exception:  # noqa: BLE001
                    continue
            for sel in THUMB_SELECTORS:
                try:
                    items = page.locator(sel)
                    n = min(await items.count(), 8)
                    for i in range(n):
                        await items.nth(i).click(timeout=1_200)
                        await page.wait_for_timeout(300)
                    if n:
                        break
                except Exception:  # noqa: BLE001
                    continue
            await page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass