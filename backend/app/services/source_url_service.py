from __future__ import annotations

from app.services.description_parser_service import (
    DescriptionParser, SourceCandidate, SourceDetection, host_of,
)
from app.utils.security import validate_public_url


class SourceUrlService:
    def __init__(self) -> None:
        self.parser = DescriptionParser()

    def detect(self, description_html: str, product_name: str, destination_url: str | None) -> SourceDetection:
        urls = self.parser.extract_urls(description_html)
        cands = self.parser.identify_source_urls(urls, product_name, host_of(destination_url or ""))
        return self.parser.select_original_source_url(cands)

    async def validate(self, url: str) -> str:
        return await validate_public_url(url, code="source_url_invalid")

    @staticmethod
    def candidate_dict(c: SourceCandidate) -> dict:
        return {"url": c.url, "score": c.score, "kind": c.kind, "reasons": c.reasons}
