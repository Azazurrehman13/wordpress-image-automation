from __future__ import annotations

from app.services.claude_service import ClaudeService
from app.services.image_download_service import ImageDownloader
from app.services.image_extraction_service import ImageExtractor
from app.services.image_processing_service import ImageProcessor
from app.services.image_selection_service import ImageSelectionService
from app.services.job_service import JobService
from app.services.playwright_service import PlaywrightService
from app.services.product_listing_service import ProductListingService
from app.services.product_search_service import ProductSearchService
from app.services.source_url_service import SourceUrlService
from app.services.wordpress_service import WordPressAutomation


class Services:
    def __init__(self) -> None:
        self.pw = PlaywrightService()
        self.wp = WordPressAutomation(self.pw)
        self.search = ProductSearchService(self.wp)
        self.source_urls = SourceUrlService()
        self.extractor = ImageExtractor(self.pw)
        self.processor = ImageProcessor()
        self.downloader = ImageDownloader(self.processor)
        self.claude = ClaudeService()
        self.selection = ImageSelectionService(self.claude)
        self.listing = ProductListingService(self.wp)
        self.jobs = JobService(self)


services = Services()
