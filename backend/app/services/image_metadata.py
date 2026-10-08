"""Image metadata built from the product TITLE (no AI call, so the wording is always consistent).

Main (featured) image : title = alt text = caption = description = the product title.
Gallery images        : the same title with a few words added, e.g. "<Title> - Side View".
"""
from __future__ import annotations

import html
import re

from app.utils.text import URL_RE

VIEW_LABELS = {
    "front_view": "Front View", "side_view": "Side View", "back_view": "Back View",
    "three_quarter_view": "Angled View", "top_view": "Top View", "detail": "Close-Up Detail",
    "feature_closeup": "Feature Close-Up", "lifestyle": "Lifestyle View", "packaging": "Packaging",
    "accessories": "Accessories", "other": "Alternate View",
}


def _clean(text: str, banned: list[str] | None = None) -> str:
    s = re.sub(r"<[^>]+>", "", html.unescape(text or ""))  # WooCommerce titles may contain &amp; / &#8211;
    s = URL_RE.sub("", s)
    for b in banned or []:
        if b:
            s = re.sub(re.escape(b), "", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip(" -\u2013\u2014,;:")


def view_label(image_type: str) -> str:
    return VIEW_LABELS.get((image_type or "other").lower(), (image_type or "other").replace("_", " ").title())


def build_metadata(title: str, image_type: str, is_main: bool, used_labels: dict[str, int] | None = None,
                   banned: list[str] | None = None) -> dict:
    """Return {"title","alt_text","caption","description"}; all four carry the same wording.
    used_labels counts how often a gallery label was already used so repeats become "Side View 2", "Side View 3"."""
    base = _clean(title, banned) or "Product"
    if is_main:
        text = base
    else:
        label = view_label(image_type)
        if used_labels is not None:
            used_labels[label] = used_labels.get(label, 0) + 1
            if used_labels[label] > 1:
                label = f"{label} {used_labels[label]}"
        text = f"{base} - {label}"
    return {"title": text, "alt_text": text, "caption": text, "description": text}


# --------------------------------------------------------------------------- alt text
# Alt text is a natural variation of the product title: every title word stays, in the same order, with 1-4 words that
# describe what is visible added before, after or inside it, e.g. for "The Row Iowa Duffle Bag":
#   "The Row Iowa Duffle Bag Black" / "The Row Iowa Black Leather Duffle Bag" / "Black The Row Iowa Duffle Bag"
ALT_MAX_LEN = 125
_DOMAIN_RE = re.compile(r"\b[\w-]+\.(?:com|net|org|co|io|shop|store|pk|uk)\b", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", html.unescape(text or "").lower())


def validate_alt(title: str, alt: str, banned: list[str] | None = None, min_extra: int = 1, max_extra: int = 4) -> bool:
    """True when alt keeps every title word in order and adds min_extra..max_extra words (no URLs / source names)."""
    alt = (alt or "").strip()
    if not alt or len(alt) > ALT_MAX_LEN or URL_RE.search(alt) or _DOMAIN_RE.search(alt):
        return False
    if any(b and b.lower() in alt.lower() for b in banned or []):
        return False
    t, a = _words(_clean(title)), _words(alt)
    if not t:
        return False
    it = iter(a)
    if not all(w in it for w in t):  # ordered subsequence: nothing dropped, nothing scrambled
        return False
    return min_extra <= len(a) - len(t) <= max_extra


def fallback_alt(title: str, image_type: str, index: int, used: set[str]) -> str:
    """Deterministic alt text built only from the title and the view type, unique within the product."""
    base = _clean(title) or "Product"
    view = view_label(image_type)
    patterns = ["{T}", "{T} {V}", "{V} of {T}", "{T} - {V}"] if index == 0 else ["{T} {V}", "{V} of {T}", "{T} - {V}", "{T}"]
    for pat in patterns:
        cand = pat.format(T=base, V=view)
        if cand.lower() not in used:
            return cand
    n = 2
    while f"{base} {view} {n}".lower() in used:
        n += 1
    return f"{base} {view} {n}"


def keep_existing_alts(title: str, items: list[dict], banned: list[str] | None = None) -> dict[int, str]:
    """items: {id, alt, default} - keep a previously generated alt that is still valid, unique and not just the default text."""
    kept: dict[int, str] = {}
    used: set[str] = set()
    for it in items:
        cur = (it.get("alt") or "").strip()
        if cur and cur != it.get("default") and cur.lower() not in used and validate_alt(title, cur, banned):
            kept[it["id"]] = cur
            used.add(cur.lower())
    return kept


def finalize_alts(title: str, items: list[dict], kept: dict[int, str], generated: dict[int, str],
                  banned: list[str] | None = None) -> dict[int, str]:
    """items in display order: {id, type}. Uses kept alts, then Claude's (if valid + unique), else the fallback."""
    out = dict(kept)
    used = {v.lower() for v in out.values()}
    for idx, it in enumerate(items):
        if it["id"] in out:
            continue
        alt = (generated.get(it["id"]) or "").strip()
        if not (alt and alt.lower() not in used and validate_alt(title, alt, banned)):
            alt = fallback_alt(title, it.get("type", "other"), idx, used)
        out[it["id"]] = alt
        used.add(alt.lower())
    return out
