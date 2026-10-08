"""Size-limited, redirect-validated HTTP fetching for untrusted source sites."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.utils.errors import AppError
from app.utils.security import validate_public_url

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    content: bytes


async def safe_fetch(
    url: str,
    *,
    max_bytes: int,
    referer: str | None = None,
    timeout: float = 25.0,
    max_redirects: int = 5,
    accept: str = "*/*",
) -> FetchResult:
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    current = await validate_public_url(url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as client:
        for _ in range(max_redirects + 1):
            try:
                async with client.stream("GET", current) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = await validate_public_url(urljoin(current, r.headers["location"]))
                        continue
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    declared = int(r.headers.get("content-length", "0") or 0)
                    if declared and declared > max_bytes:
                        raise AppError("file_too_large", "Remote file exceeds the size limit.", 413)
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            raise AppError("file_too_large", "Remote file exceeds the size limit.", 413)
                    return FetchResult(current, r.status_code, ctype, bytes(buf))
            except httpx.HTTPError as e:
                raise AppError("source_unavailable", f"Could not reach {current}: {type(e).__name__}", 502)
    raise AppError("source_unavailable", "Too many redirects.", 502)
