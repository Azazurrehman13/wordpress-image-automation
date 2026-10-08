"""Finds the original image/product source URL inside an existing product's description."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.utils.text import URL_RE, clean_trailing, tokens

SOCIAL_HOSTS = (
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "t.co", "linkedin.com",
    "pinterest.com", "tiktok.com", "youtube.com", "youtu.be", "wa.me", "whatsapp.com", "t.me",
    "telegram.me", "snapchat.com", "reddit.com", "google.com", "goo.gl", "schema.org", "w3.org",
    "gravatar.com", "wordpress.org", "wordpress.com",
)
UTILITY_WORDS = (
    "privacy", "terms", "contact", "about", "cart", "checkout", "login", "my-account", "wp-login",
    "wp-admin", "shipping", "returns", "refund", "policy", "faq", "unsubscribe", "cookie", "sitemap",
)
CONTEXT_WORDS = ("original", "source", "image source", "src", "credit", "courtesy", "via", "photo", "image", "picture")
PRODUCT_PATH_HINTS = ("/product/", "/products/", "/item/", "/items/", "/p/", "/dp/", "/shop/", "/catalog/", "/gp/")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "ref", "aff", "affiliate", "affid", "tag", "mc_cid", "mc_eid"}
LABEL_ONLY = re.compile(
    r"^\s*(original|image|images|source|source url|original image|original product|image source|"
    r"original source|photo|photo source|credit|via)\s*[:\-–—]?\s*$",
    re.IGNORECASE,
)


@dataclass
class ExtractedUrl:
    url: str
    anchor_text: str = ""
    context: str = ""
    from_anchor: bool = False
    wraps_image: bool = False
    is_img_src: bool = False


@dataclass
class SourceCandidate:
    url: str
    score: float
    kind: str  # page | image_file
    reasons: list[str] = field(default_factory=list)


@dataclass
class SourceDetection:
    candidates: list[SourceCandidate]
    selected: SourceCandidate | None
    ambiguous: bool = False


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def clean_url(url: str) -> str:
    """Drop tracking parameters and fragments from a URL used for fetching."""
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


class DescriptionParser:
    # ---------------------------------------------------------------- extract
    def extract_urls(self, description: str) -> list[ExtractedUrl]:
        out: dict[str, ExtractedUrl] = {}
        soup = BeautifulSoup(description or "", "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue
            prev = a.find_previous(string=True)
            ctx = ""
            node = a
            # text immediately before the anchor, within the same block
            sib = a.previous_sibling
            while sib is not None and len(ctx) < 80:
                ctx = (sib.get_text(" ") if hasattr(sib, "get_text") else str(sib)) + " " + ctx
                sib = sib.previous_sibling
            if not ctx.strip() and prev:
                ctx = str(prev)
            out.setdefault(href, ExtractedUrl(
                url=href, anchor_text=a.get_text(" ", strip=True), context=ctx.strip()[-80:],
                from_anchor=True, wraps_image=a.find("img") is not None,
            ))
        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if src.lower().startswith(("http://", "https://")) and src not in out:
                out[src] = ExtractedUrl(url=src, is_img_src=True)

        text = soup.get_text("\n")
        for m in URL_RE.finditer(text):
            u = clean_trailing(m.group(0))
            if u in out:
                continue
            start = max(0, m.start() - 80)
            out[u] = ExtractedUrl(url=u, context=text[start:m.start()].strip())
        return list(out.values())

    # --------------------------------------------------------------- identify
    def identify_source_urls(self, urls: list[ExtractedUrl], product_name: str = "",
                             destination_host: str = "") -> list[SourceCandidate]:
        dest = destination_host.lower().removeprefix("www.")
        name_tokens = {t for t in tokens(product_name) if len(t) > 1}
        cands: dict[str, SourceCandidate] = {}
        for eu in urls:
            try:
                p = urlsplit(eu.url)
            except ValueError:
                continue
            host = host_of(eu.url)
            if p.scheme not in ("http", "https") or not host:
                continue
            if dest and _host_matches(host, dest):
                continue  # links back to the destination are never the source
            if any(_host_matches(host, d) for d in SOCIAL_HOSTS):
                continue
            path = p.path.lower()
            if any(w in path.strip("/").split("/")[-1] or f"/{w}" in path for w in UTILITY_WORDS):
                continue
            is_image = path.endswith(IMAGE_EXT)
            score, reasons = 10.0, []
            ctx = (eu.context + " " + eu.anchor_text).lower()
            if any(w in eu.context.lower() for w in CONTEXT_WORDS):
                score += 30
                reasons.append("labelled as source/original in surrounding text")
            if any(w in eu.anchor_text.lower() for w in CONTEXT_WORDS):
                score += 15
                reasons.append("link text mentions source/original")
            if any(h in path for h in PRODUCT_PATH_HINTS):
                score += 25
                reasons.append("product-style URL path")
            slug_tokens = set(tokens(path.replace("/", " ")))
            if name_tokens:
                overlap = len(name_tokens & slug_tokens) / len(name_tokens)
                if overlap:
                    score += round(30 * overlap, 1)
                    reasons.append(f"URL matches {int(overlap * 100)}% of the product name")
            if eu.wraps_image:
                score += 10
                reasons.append("link wraps an image")
            if is_image:
                score -= 5
                reasons.append("direct image file")
            if p.path in ("", "/"):
                score -= 30
                reasons.append("homepage only")
            if "sources" in ctx and "original" in ctx:
                score += 5
            key = clean_url(eu.url)
            cand = SourceCandidate(url=key if not is_image else eu.url, score=round(score, 1),
                                   kind="image_file" if is_image else "page", reasons=reasons)
            if key not in cands or cands[key].score < cand.score:
                cands[key] = cand
        return sorted(cands.values(), key=lambda c: c.score, reverse=True)

    MIN_SCORE = 30.0

    def select_original_source_url(self, candidates: list[SourceCandidate]) -> SourceDetection:
        good = [c for c in candidates if c.score >= self.MIN_SCORE]
        if not good:
            return SourceDetection(candidates=candidates, selected=None)
        ambiguous = len(good) > 1 and (good[0].score - good[1].score) < 8
        return SourceDetection(candidates=candidates, selected=good[0], ambiguous=ambiguous)

    # ------------------------------------------------------------------ clean
    def remove_source_urls(self, html: str, source_urls: list[str]) -> str:
        """Remove links/mentions of the source from product content (anchors, plain URLs, label-only lines)."""
        hosts = {host_of(u) for u in source_urls if u}
        hosts.discard("")
        if not html or not hosts:
            return html

        def matches(u: str) -> bool:
            return any(_host_matches(host_of(u), h) for h in hosts)

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if matches(a["href"]):
                if a.find("img"):
                    a.unwrap()
                else:
                    a.decompose()
        for img in soup.find_all("img", src=True):
            if matches(img["src"]):
                img.decompose()
        for node in soup.find_all(string=True):
            s = str(node)
            if URL_RE.search(s):
                new = URL_RE.sub(lambda m: "" if matches(clean_trailing(m.group(0))) else m.group(0), s)
                if new != s:
                    node.replace_with(new)
        for el in soup.find_all(["p", "li", "div", "span"]):
            txt = el.get_text(" ", strip=True)
            if not el.find("img") and (txt == "" or LABEL_ONLY.match(txt)):
                el.decompose()
        out = str(soup)
        out = "\n".join(line for line in out.splitlines() if not LABEL_ONLY.match(re.sub(r"<[^>]+>", "", line)))
        return re.sub(r"\n{3,}", "\n\n", out).strip()


    # ------------------------------------------------------- link-only clean
    def remove_source_links(self, html: str, source_urls: list[str]) -> str:
        """Remove ONLY the source link(s) and leave every other character of the description untouched.
        Works on the raw HTML text (no re-serialisation), so formatting, shortcodes and wording stay as they were.
        Wrapper tags (<p>, <li>, <span>, <div>) that become empty because of the removal are dropped too."""
        hosts = {host_of(u) for u in source_urls if u}
        hosts.discard("")
        if not html or not hosts:
            return html

        def matches(u: str) -> bool:
            return any(_host_matches(host_of(u), h) for h in hosts)

        mark = "\x00"
        anchor = re.compile(r"<a\b[^>]*?\bhref\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a\s*>", re.I | re.S)

        def anchor_repl(m: re.Match) -> str:
            if not matches(m.group(2).strip()):
                return m.group(0)
            inner = m.group(3)
            return inner if re.search(r"<img\b", inner, re.I) else mark  # image links keep their image

        out = anchor.sub(anchor_repl, html)

        # bare URLs in text (never inside tags / attributes)
        parts = re.split(r"(<[^>]*>)", out)
        for i in range(0, len(parts), 2):
            if URL_RE.search(parts[i]):
                parts[i] = URL_RE.sub(lambda m: mark if matches(clean_trailing(m.group(0))) else m.group(0), parts[i])
        out = "".join(parts)

        if mark in out:
            blank = rf"(?:\s|&nbsp;|<br\s*/?>|{mark})*"
            out = re.sub(rf"<(p|li|span|div)\b[^>]*>{blank}{mark}{blank}</\1\s*>[ \t]*(?:\r?\n)?", mark, out, flags=re.I)
            out = out.replace(mark, "")
        return out
