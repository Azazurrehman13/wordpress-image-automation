from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.config import cfg
from app.services.image_extraction_service import ImageCandidate
from app.services.image_processing_service import ImageProcessor
from app.utils.errors import AppError
from app.utils.http import safe_fetch

FIRST_WAVE = 16          # best-ranked candidates are downloaded first (gallery images come first)
ENOUGH_VALID = 8         # the rest are only downloaded when fewer than this many usable images were found
CONCURRENCY = 8          # parallel downloads
PER_IMAGE_TIMEOUT = 30   # seconds for one candidate including its fallback URLs; a hanging image can no longer stall the step


@dataclass
class DownloadResult:
    ok: bool
    url: str
    path: str = ""
    width: int = 0
    height: int = 0
    size: int = 0
    reason: str = ""


class ImageDownloader:
    def __init__(self, processor: ImageProcessor) -> None:
        self.processor = processor

    async def _try(self, idx: int, cand: ImageCandidate, dest: Path, referer: str) -> DownloadResult:
        last = "Download failed"
        for url in [cand.url, *cand.fallbacks]:
            try:
                r = await safe_fetch(url, max_bytes=cfg.max_download_bytes, referer=referer, accept="image/*,*/*;q=0.5")
            except AppError as e:
                last = e.message
                continue
            if r.status != 200:
                last = f"HTTP {r.status}"
                continue
            # decoding/validating and writing are CPU/disk work: keep them off the event loop so other downloads keep running
            v = await asyncio.to_thread(self.processor.validate_image, r.content, url)
            if not v.ok:
                last = v.reason
                if "site graphic" in v.reason:
                    break
                continue
            path = dest / f"img_{idx:03d}.{v.ext}"
            await asyncio.to_thread(path.write_bytes, r.content)
            return DownloadResult(True, url, str(path), v.width, v.height, len(r.content))
        return DownloadResult(False, cand.url, reason=last)

    async def _one(self, idx: int, cand: ImageCandidate, dest: Path, referer: str, sem: asyncio.Semaphore) -> DownloadResult:
        async with sem:
            try:
                return await asyncio.wait_for(self._try(idx, cand, dest, referer), PER_IMAGE_TIMEOUT)
            except asyncio.TimeoutError:
                return DownloadResult(False, cand.url, reason=f"Timed out after {PER_IMAGE_TIMEOUT}s")

    async def _wave(self, cands: list[ImageCandidate], first_idx: int, dest: Path, referer: str) -> list[DownloadResult]:
        sem = asyncio.Semaphore(CONCURRENCY)
        return list(await asyncio.gather(*[self._one(first_idx + i, c, dest, referer, sem) for i, c in enumerate(cands)]))

    async def download_all(self, job_id: str, candidates: list[ImageCandidate], referer: str) -> list[DownloadResult]:
        dest = cfg.jobs_dir / job_id / "source"
        dest.mkdir(parents=True, exist_ok=True)
        picked = candidates[: cfg.max_candidates]
        results = await self._wave(picked[:FIRST_WAVE], 1, dest, referer)
        if sum(1 for r in results if r.ok) < ENOUGH_VALID and len(picked) > FIRST_WAVE:
            results += await self._wave(picked[FIRST_WAVE:], FIRST_WAVE + 1, dest, referer)
        return results