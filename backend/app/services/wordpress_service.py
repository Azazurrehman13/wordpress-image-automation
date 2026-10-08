"""WordPress/WooCommerce automation.

Playwright performs the real login (persistent session). Writes then go through the
REST API using the same session cookies + the REST nonce, which is far more reliable than
driving the admin UI. Playwright remains the fallback for search and description reading.
"""
from __future__ import annotations

import asyncio
import json as _json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from playwright.async_api import Error as PlaywrightError

from app.config import cfg
from app.services.playwright_service import PlaywrightService
from app.utils.errors import AppError
from app.utils.logging_utils import register_secret
from app.utils.security import sanitize_filename

DESC_SELECTORS = [
    "#tab-description", ".woocommerce-Tabs-panel--description",
    ".woocommerce-product-details__short-description", "[itemprop=description]",
    ".product .entry-content", "div.summary .product_meta + div",
]

# Connection-level failures worth retrying (the site dropped the connection, it did not answer "no").
TRANSIENT_ERRORS = ("socket hang up", "econnreset", "econnaborted", "epipe", "etimedout", "timeout", "timed out",
                    "net::err_", "connection closed", "connection reset", "network")
RETRY_DELAY = 1.5  # seconds, multiplied by the attempt number


def _is_idempotent(method: str, route: str) -> bool:
    """Repeating the call cannot create a second item: reads, PUT/DELETE, and POST updates such as media/123."""
    if method.upper() != "POST":
        return True
    return bool(re.match(r"^(?:wp/v2|wc/v3)/[\w-]+/\d+/?$", route.strip("/")))


@dataclass
class RestResponse:
    status: int
    data: object
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class ProductDescription:
    id: int | None
    name: str
    permalink: str
    status: str
    description_html: str = ""
    short_description_html: str = ""
    page_description_html: str = ""

    @property
    def combined_html(self) -> str:
        parts = [self.description_html, self.short_description_html, self.page_description_html]
        seen, out = set(), []
        for p in parts:
            if p and p.strip() and p.strip() not in seen:
                seen.add(p.strip())
                out.append(p)
        return "\n".join(out)


class WordPressAutomation:
    def __init__(self, pw: PlaywrightService) -> None:
        self.pw = pw
        self.base_url: str | None = None
        self.login_url: str | None = None
        self.username: str | None = None
        self._password: str | None = None  # memory only; never logged or persisted
        self._nonce: str | None = None
        self._use_rest_route = False
        self.connected = False

    # ------------------------------------------------------------ connection
    @staticmethod
    def derive_base_url(login_url: str) -> str:
        p = urlsplit(login_url.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            raise AppError("wordpress_login_failed", "Enter a full login URL such as https://example.com/wp-login.php", 422)
        path = p.path
        for marker in ("wp-login.php", "/wp-admin"):
            if marker in path:
                path = path.split(marker)[0]
        return f"{p.scheme}://{p.netloc}{path.rstrip('/')}"

    async def connect(self, login_url: str, username: str, password: str, headless: bool | None = None) -> None:
        self.base_url = self.derive_base_url(login_url)
        self.login_url = login_url.strip()
        self.username = username
        self._password = password
        register_secret(password)
        self.connected = False
        await self.pw.start(headless)
        await self.login()

    def _meta_path(self) -> Path:
        return cfg.profile_dir / "session_meta.json"

    def _session_matches(self) -> bool:
        try:
            m = _json.loads(self._meta_path().read_text("utf-8"))
            return m.get("base_url") == self.base_url and m.get("username") == self.username
        except Exception:  # noqa: BLE001
            return False

    async def _logged_in_cookie(self) -> bool:
        cookies = await self.pw.context.cookies(self.base_url)
        return any(c["name"].startswith("wordpress_logged_in") for c in cookies)

    async def login(self, force_fresh: bool = False) -> None:
        ctx = self.pw.context
        page = await ctx.new_page()
        try:
            reused = False
            if not force_fresh and self._session_matches() and await self._logged_in_cookie():
                await self._goto(page, f"{self.base_url}/wp-admin/")
                reused = "wp-login.php" not in page.url and await self._logged_in_cookie()
            if not reused:
                await ctx.clear_cookies()
                await self._goto(page, self.login_url or "")
                if not await page.query_selector("#user_login"):
                    raise AppError("wordpress_login_failed", "No WordPress login form found at that URL.", 400)
                await page.fill("#user_login", self.username or "")
                await page.fill("#user_pass", self._password or "")
                if await page.query_selector("#rememberme"):
                    await page.check("#rememberme")
                await page.click("#wp-submit")
                try:
                    await page.wait_for_url(lambda u: "wp-login.php" not in u, timeout=25_000)
                except Exception:  # noqa: BLE001
                    err = await page.query_selector("#login_error")
                    if err:
                        raise AppError("wordpress_login_failed", "WordPress rejected the username or password.", 401)
                    raise AppError(
                        "wordpress_login_needs_interaction",
                        "Login needs extra verification (2FA or captcha). Turn Headless Browser off, connect again, and finish the step in the browser window.",
                        401,
                    )
                if not await self._logged_in_cookie():
                    raise AppError("wordpress_login_failed", "Login did not complete.", 401)
            self._meta_path().write_text(_json.dumps({"base_url": self.base_url, "username": self.username}), "utf-8")
        finally:
            await page.close()
        try:
            await self.refresh_nonce()
        except AppError as e:
            if e.code == "wordpress_rest_unavailable" and reused and not force_fresh:
                await self.login(force_fresh=True)  # the saved session was stale: log in again from scratch, once
                return
            raise
        self.connected = True

    async def logout(self) -> None:
        self.connected = False
        self._nonce = self._password = None
        await self.pw.clear_session()

    NONCE_PATTERNS = (
        r'createNonceMiddleware\(\s*["\']([A-Za-z0-9]{6,20})["\']',
        r'wpApiSettings\s*=\s*\{[^}]*?"nonce"\s*:\s*"([A-Za-z0-9]{6,20})"',
        r"wpApiSettings.*?nonce\W+([A-Za-z0-9]{6,20})",
        r'"nonce"\s*:\s*"([A-Za-z0-9]{6,20})"',
    )

    async def refresh_nonce(self) -> None:
        """Get the REST nonce: admin-ajax first, then admin pages that always embed it. Says exactly what went wrong if none works."""
        ctx = self.pw.context
        notes: list[str] = []
        try:
            r = await self._fetch(f"{self.base_url}/wp-admin/admin-ajax.php?action=rest-nonce", {"method": "GET", "timeout": 60_000}, "rest-nonce", retries=2)
            txt = (await r.text()).strip()
            if r.ok and re.fullmatch(r"[A-Za-z0-9]{6,20}", txt):
                self._nonce = txt
                return
            notes.append(f"admin-ajax answered HTTP {r.status}")
        except AppError as e:
            notes.append(e.message)
        for path in ("/wp-admin/", "/wp-admin/post-new.php?post_type=product", "/wp-admin/post-new.php"):
            page = await ctx.new_page()
            try:
                await self._goto(page, f"{self.base_url}{path}")
                landed, html = page.url, await page.content()
            except AppError as e:
                notes.append(e.message)
                continue
            finally:
                await page.close()
            if "wp-login.php" in landed:
                notes.append("the saved login is no longer valid (WordPress sent the browser back to the login page)")
                break
            for pat in self.NONCE_PATTERNS:
                m = re.search(pat, html, re.S)
                if m:
                    self._nonce = m.group(1)
                    return
            notes.append(f"no REST token found on {path}")
        raise AppError(
            "wordpress_rest_unavailable",
            "Logged in, but could not obtain a REST API token (" + "; ".join(notes) + "). If this keeps happening, the host's firewall "
            "or a security plugin may be blocking the automated browser, or the REST API is disabled.",
            502,
        )

    # ------------------------------------------------------------------ REST
    async def rest(self, method: str, route: str, *, params: dict | None = None, data=None,
                   headers: dict | None = None, _retry: bool = True) -> RestResponse:
        route = route.lstrip("/")
        if self._use_rest_route:
            url = f"{self.base_url}/?rest_route=/{route}"
        else:
            url = f"{self.base_url}/wp-json/{route}"
        h = {"X-WP-Nonce": self._nonce or "", "Accept": "application/json"}
        if headers:
            h.update(headers)
        kw: dict = {"method": method, "headers": h, "timeout": 90_000}
        if params:
            kw["params"] = params
        if data is not None:
            kw["data"] = data
        resp = await self._fetch(url, kw, route, retries=3 if _is_idempotent(method, route) else 0)
        body = await resp.body()
        try:
            parsed = _json.loads(body) if body else None
        except ValueError:
            parsed = None
        if resp.status == 404 and parsed is None and not self._use_rest_route:
            self._use_rest_route = True
            return await self.rest(method, route, params=params, data=data, headers=headers, _retry=False)
        if _retry and resp.status in (401, 403) and isinstance(parsed, dict) and parsed.get("code") == "rest_cookie_invalid_nonce":
            await self.refresh_nonce()
            return await self.rest(method, route, params=params, data=data, headers=headers, _retry=False)
        return RestResponse(resp.status, parsed, dict(resp.headers))

    async def _fetch(self, url: str, kw: dict, route: str, retries: int):
        """Playwright request with retries for dropped connections ("socket hang up" and similar)."""
        for attempt in range(retries + 1):
            try:
                return await self.pw.context.request.fetch(url, **kw)
            except PlaywrightError as e:
                msg = str(e).strip().splitlines()[0] if str(e).strip() else "request failed"
                transient = any(t in msg.lower() for t in TRANSIENT_ERRORS)
                if transient and attempt < retries:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise AppError("wordpress_unreachable", f"WordPress dropped the connection while calling {route}: {msg}", 502) from e
        raise AppError("wordpress_unreachable", f"WordPress did not answer {route}.", 502)  # pragma: no cover

    # Network errors that will not fix themselves: do not wait and retry.
    PERMANENT_NET = ("err_name_not_resolved", "err_cert", "err_invalid_url", "err_ssl", "err_unsafe_port")

    @staticmethod
    def _explain_network_error(url: str, msg: str) -> str:
        low = msg.lower()
        if "err_name_not_resolved" in low:
            return f"The domain in {url} could not be found. Check the login URL for typing mistakes."
        if "err_cert" in low or "err_ssl" in low:
            return f"The SSL certificate of {url} is not valid ({msg})."
        if "timed_out" in low or "timeout" in low or "err_connection" in low:
            return (f"{url} did not answer ({msg}). This computer cannot reach the site right now. Open the site in a normal browser: "
                    "if it also fails, check your internet/VPN, or your host's firewall may be blocking your IP after many automated requests "
                    "(wait a while or ask the host to allow your IP). If it opens normally, the host is blocking the automated browser: "
                    "turn Headless Browser off and connect again.")
        return f"Could not open {url}: {msg}"

    async def _goto(self, page, url: str, *, tries: int = 2, timeout: int = 40_000) -> None:
        """page.goto with one retry; raw Playwright network errors become a clear, actionable message instead of a crash."""
        last = ""
        for attempt in range(tries):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                return
            except PlaywrightError as e:
                last = (str(e).strip().splitlines() or ["request failed"])[0].replace("Page.goto: ", "")
                if any(p in last.lower() for p in self.PERMANENT_NET) or attempt == tries - 1:
                    break
                await asyncio.sleep(2 * (attempt + 1))
        raise AppError("wordpress_unreachable", self._explain_network_error(url, last), 502)

    @staticmethod
    def _err(r: RestResponse) -> str:
        if isinstance(r.data, dict) and r.data.get("message"):
            return f"{r.data['message']} (HTTP {r.status})"
        return f"HTTP {r.status}"

    # --------------------------------------------------------------- search
    @staticmethod
    def _norm_wc(p: dict) -> dict:
        return {"id": p["id"], "name": p.get("name", ""), "permalink": p.get("permalink", ""), "status": p.get("status", "")}

    @staticmethod
    def _norm_wp(p: dict) -> dict:
        t = p.get("title", {})
        return {"id": p["id"], "name": t.get("raw") or t.get("rendered", ""), "permalink": p.get("link", ""), "status": p.get("status", "")}

    async def search_product(self, product_name: str) -> list[dict]:
        """Products matching the name, limited to cfg.search_statuses (default: draft only - published products are not listed).
        Copies ("... (Copy)") are never listed."""
        wanted = list(cfg.search_statuses)
        found: dict[int, dict] = {}
        rest_ok = False
        for st in wanted:  # WooCommerce accepts one status per request
            r = await self.rest("GET", "wc/v3/products", params={"search": product_name, "per_page": 20, "status": st})
            if r.ok and isinstance(r.data, list):
                rest_ok = True
                found.update({p["id"]: self._norm_wc(p) for p in r.data})
        if not rest_ok:
            r = await self.rest("GET", "wp/v2/product", params={"search": product_name, "per_page": 20, "status": ",".join(wanted), "context": "edit"})
            if r.ok and isinstance(r.data, list):
                rest_ok = True
                found.update({p["id"]: self._norm_wp(p) for p in r.data})
        if not rest_ok:
            found = {i: m for i, m in enumerate(await self._browser_search(product_name))}
        return [m for m in found.values() if m["status"] in wanted and not is_copy_product(m["name"])][:20]  # the site's public search only sees published items -> dropped here

    async def _browser_search(self, product_name: str) -> list[dict]:
        page = await self.pw.context.new_page()
        try:
            await self._goto(page, f"{self.base_url}/?s={quote(product_name)}&post_type=product")
            links = await page.eval_on_selector_all(
                "a[href*='/product/']",
                "els => els.map(e => ({href: e.href, text: (e.innerText || e.title || '').trim()}))",
            )
        finally:
            await page.close()
        seen, out = set(), []
        for l in links:
            if l["href"] in seen or not l["text"] or "?add-to-cart" in l["href"]:
                continue
            seen.add(l["href"])
            out.append({"id": None, "name": l["text"].split("\n")[0], "permalink": l["href"], "status": "publish"})
        return out[:20]

    # ------------------------------------------------------- product reading
    async def open_product(self, product_url: str) -> dict:
        """Open the real product page and read what a visitor/editor sees."""
        page = await self.pw.context.new_page()
        try:
            await self._goto(page, product_url)
            html_parts = []
            for sel in DESC_SELECTORS:
                for el in await page.query_selector_all(sel):
                    inner = await el.inner_html()
                    if inner.strip():
                        html_parts.append(inner)
            title = (await page.title()) or ""
            shortlink = await page.eval_on_selector("link[rel=shortlink]", "e => e.href") if await page.query_selector("link[rel=shortlink]") else ""
            body_class = await page.eval_on_selector("body", "e => e.className")
        finally:
            await page.close()
        pid = None
        m = re.search(r"[?&]p=(\d+)", shortlink or "") or re.search(r"postid-(\d+)", body_class or "")
        if m:
            pid = int(m.group(1))
        return {"id": pid, "title": title, "html": "\n".join(html_parts)}

    async def get_product_description(self, product_id: int | None = None, product_url: str | None = None) -> ProductDescription:
        page_data = {"id": None, "title": "", "html": ""}
        if product_url:
            try:
                page_data = await self.open_product(product_url)
            except Exception:  # noqa: BLE001
                pass
        pid = product_id or page_data["id"]
        if pid is None:
            raise AppError("product_not_found", "Could not determine the WooCommerce product ID.", 404)
        r = await self.rest("GET", f"wc/v3/products/{pid}")
        if r.ok and isinstance(r.data, dict):
            d = r.data
            return ProductDescription(pid, d.get("name", ""), d.get("permalink", product_url or ""), d.get("status", ""),
                                      d.get("description", ""), d.get("short_description", ""), page_data["html"])
        r = await self.rest("GET", f"wp/v2/product/{pid}", params={"context": "edit"})
        if r.ok and isinstance(r.data, dict):
            d = r.data
            c = d.get("content", {})
            return ProductDescription(pid, (d.get("title", {}) or {}).get("raw", ""), d.get("link", product_url or ""),
                                      d.get("status", ""), c.get("raw") or c.get("rendered", ""), "", page_data["html"])
        if page_data["html"]:
            return ProductDescription(pid, page_data["title"], product_url or "", "publish", "", "", page_data["html"])
        raise AppError("product_description_unavailable", f"Could not read the product: {self._err(r)}", 502)

    # ----------------------------------------------------------------- media
    async def upload_media(self, image_path: Path, metadata: dict) -> dict:
        filename = sanitize_filename(metadata.get("filename") or image_path.name, "image.jpg")
        mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
        data = image_path.read_bytes()
        r = None
        for attempt in range(3):
            try:
                r = await self.rest("POST", "wp/v2/media", data=data, headers={
                    "Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": mime})
            except AppError as e:  # connection dropped during the upload: try again, then give a clear error
                if e.code != "wordpress_unreachable" or attempt == 2:
                    raise
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                continue
            if r.ok or r.status < 500:
                break
        if r is None or not r.ok or not isinstance(r.data, dict):
            raise AppError("wordpress_upload_failed", f"WordPress rejected {filename}: {self._err(r) if r else 'no answer'}", 502)
        mid = r.data["id"]
        try:  # POST media/<id> is an update, so rest() retries it by itself if the connection drops
            r2 = await self.rest("POST", f"wp/v2/media/{mid}", data={
                "title": metadata.get("title", ""), "alt_text": metadata.get("alt_text", ""),
                "caption": metadata.get("caption", ""), "description": metadata.get("description", ""),
            })
            problem = None if r2.ok else self._err(r2)
        except AppError as e:
            problem = e.message
        if problem:
            try:
                await self.delete_media(mid)  # do not leave a half-finished upload in the media library
            except AppError:
                pass
            raise AppError("wordpress_upload_failed", f"Uploaded {filename} but could not save its metadata: {problem}", 502)
        return {"id": mid, "source_url": r.data.get("source_url", "")}

    async def update_media_metadata(self, media_id: int, metadata: dict) -> None:
        """Rewrite title / alt text / caption / description of an already uploaded media item."""
        r = await self.rest("POST", f"wp/v2/media/{media_id}", data={
            "title": metadata.get("title", ""), "alt_text": metadata.get("alt_text", ""),
            "caption": metadata.get("caption", ""), "description": metadata.get("description", "")})
        if not r.ok:
            raise AppError("wordpress_upload_failed", f"Could not update the metadata of media {media_id}: {self._err(r)}", 502)

    async def delete_media(self, media_id: int) -> bool:
        r = await self.rest("DELETE", f"wp/v2/media/{media_id}", params={"force": "true"})
        return r.ok

    # -------------------------------------------------------------- products
    async def get_product(self, product_id: int, raw: bool = False) -> dict:
        """raw=True returns the stored content (context=edit): no wpautop, shortcodes not expanded."""
        r = await self.rest("GET", f"wc/v3/products/{product_id}", params={"context": "edit"} if raw else None)
        if not r.ok or not isinstance(r.data, dict):
            raise AppError("product_not_found", f"Could not load product {product_id}: {self._err(r)}", 502)
        return r.data

    async def create_or_update_product(self, product_id: int | None, *, name: str, media_ids: list[int],
                                       description: str | None = None, short_description: str | None = None,
                                       status: str | None = None) -> dict:
        """Only touches images (+ cleaned description/status when given). In the WooCommerce
        `images` array the first item is the Product Image and the rest are the Gallery, in order."""
        payload: dict = {"images": [{"id": m} for m in media_ids]}
        if description is not None:
            payload["description"] = description
        if short_description is not None:
            payload["short_description"] = short_description
        if product_id is None:
            payload.update({"name": name, "type": "simple", "status": status or "draft"})
            r = await self.rest("POST", "wc/v3/products", data=payload)
        else:
            if status:
                payload["status"] = status
            r = await self.rest("PUT", f"wc/v3/products/{product_id}", data=payload)
        if not r.ok or not isinstance(r.data, dict):
            raise AppError("product_update_failed", f"WooCommerce rejected the product update: {self._err(r)}", 502)
        return r.data

    async def set_featured_image(self, product_id: int, media_id: int) -> dict:
        cur = await self.get_product(product_id)
        rest = [i["id"] for i in cur.get("images", [])[1:] if i["id"] != media_id]
        return await self.create_or_update_product(product_id, name=cur.get("name", ""), media_ids=[media_id] + rest)

    async def set_gallery_images(self, product_id: int, media_ids: list[int]) -> dict:
        cur = await self.get_product(product_id)
        first = [cur["images"][0]["id"]] if cur.get("images") else []
        return await self.create_or_update_product(
            product_id, name=cur.get("name", ""), media_ids=first + [m for m in media_ids if m not in first])

    async def publish_product(self, product_id: int) -> dict:
        cur = await self.get_product(product_id)
        if is_copy_product(cur.get("name", "")):  # copies are never published, whatever called this
            raise AppError("publish_blocked_copy", f"'{cur.get('name', '')}' is a copy and is never published.", 409)
        r = await self.rest("PUT", f"wc/v3/products/{product_id}", data={"status": "publish"})
        if not r.ok or not isinstance(r.data, dict):
            raise AppError("publish_failed", f"Could not publish the product: {self._err(r)}", 502)
        return r.data

    # ---------------------------------------------------------------- verify
    async def verify_product(self, product_id: int, expected_media_ids: list[int], forbidden: list[str]) -> list[dict]:
        checks: list[dict] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        try:
            p = await self.get_product(product_id)
        except AppError as e:
            return [{"name": "Product exists", "ok": False, "detail": e.message}]
        add("Product exists", True, p.get("name", ""))
        add("Product is public (not private/draft)", p.get("status") == "publish", f"status is '{p.get('status')}'")
        imgs = p.get("images", [])
        ids = [i["id"] for i in imgs]
        add("Main Product Image exists", bool(ids))
        add("Main image is the best selected image", bool(ids) and ids[0] == expected_media_ids[0],
            f"expected {expected_media_ids[0]}, got {ids[0] if ids else None}")
        add("Gallery images exist", len(ids) > 1 or len(expected_media_ids) == 1)
        add("Gallery order is correct", ids == expected_media_ids, f"expected {expected_media_ids}, got {ids}")
        add("Image metadata exists", bool(imgs) and all((i.get("alt") or "").strip() and (i.get("name") or "").strip() for i in imgs))
        blob = " ".join([p.get("description", ""), p.get("short_description", "")] + [i.get("src", "") + i.get("alt", "") for i in imgs])
        leaks = [f for f in forbidden if f and f.lower() in blob.lower()]
        add("Source URL is not present", not leaks, ", ".join(leaks))
        link = p.get("permalink", "")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
                resp = await c.get(link)
            add("Product page loads successfully", resp.status_code == 200, f"HTTP {resp.status_code}")
            # fetched without the admin login, so a 200 here means any visitor can open it
            add("Product page is visible to visitors (no password)", resp.status_code == 200 and "post-password-form" not in resp.text)
            page_leaks = [f for f in forbidden if f and f.lower() in resp.text.lower()]
            if page_leaks and resp.status_code == 200:
                add("Source URL absent from the public page", False, ", ".join(page_leaks))
        except httpx.HTTPError as e:
            add("Product page loads successfully", False, type(e).__name__)
        return checks


def is_copy_product(name: str) -> bool:
    """True for duplicated products such as "Pouch (Copy)", "Pouch - Copy 2" or "Copy of Pouch". Copies are never processed."""
    n = (name or "").strip()
    return bool(re.search(r"\(\s*copy\s*\d*\s*\)|[-–—]\s*copy\s*\d*\s*$|\bcopy\s*\d*\s*$|^copy of\b", n, re.I))