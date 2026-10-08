"""Applies the approved images to the destination product and verifies the result."""
from __future__ import annotations

from app.services.description_parser_service import DescriptionParser, host_of
from app.services.wordpress_service import WordPressAutomation


class ProductListingService:
    def __init__(self, wp: WordPressAutomation) -> None:
        self.wp = wp
        self.parser = DescriptionParser()

    def clean_content(self, product: dict, source_url: str | None, strip: bool) -> tuple[str | None, str | None]:
        """Return (description, short_description) with ONLY the source link removed, or None when unchanged.
        Pass the RAW product content (context=edit) so nothing else in the description changes."""
        if not strip or not source_url:
            return None, None
        out = []
        for key in ("description", "short_description"):
            old = product.get(key, "") or ""
            new = self.parser.remove_source_links(old, [source_url])
            out.append(new if new != old else None)
        return out[0], out[1]

    @staticmethod
    def forbidden_strings(source_url: str | None) -> list[str]:
        if not source_url:
            return []
        h = host_of(source_url)
        return [s for s in {source_url, source_url.rstrip("/"), h} if s]
