from __future__ import annotations

from app.services.wordpress_service import WordPressAutomation
from app.utils.text import match_score


class ProductSearchService:
    def __init__(self, wp: WordPressAutomation) -> None:
        self.wp = wp

    async def search(self, query: str) -> list[dict]:
        raw = await self.wp.search_product(query)
        scored = [{**p, "score": match_score(query, p["name"])} for p in raw]
        return sorted(scored, key=lambda p: p["score"], reverse=True)

    @staticmethod
    def auto_select(matches: list[dict], threshold: float) -> dict | None:
        """Only auto-select a clear winner. Ambiguous or low-confidence results need the user."""
        if not matches:
            return None
        top = matches[0]
        if top["score"] < threshold:
            return None
        if len(matches) > 1 and top["score"] - matches[1]["score"] < 10 and top["score"] < 100:
            return None
        return top
