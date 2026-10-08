"""URL and filename safety helpers (SSRF protection, sanitising)."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from app.config import cfg
from app.utils.errors import AppError

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str, default: str = "file", max_len: int = 80) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("-", name).strip("-._")
    return name[:max_len] or default


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].strip("-") or "product"


def _ip_ok(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_global
    except ValueError:
        return False


async def validate_public_url(url: str, allow_private: bool | None = None, code: str = "source_url_invalid") -> str:
    """Return the URL or raise AppError. Blocks non-http(s), credentials and private hosts."""
    allow_private = cfg.allow_private_source_urls if allow_private is None else allow_private
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise AppError(code, "The URL is not valid.", 422)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AppError(code, "Only http(s) URLs are allowed.", 422)
    if parts.username or parts.password:
        raise AppError(code, "URLs containing credentials are not allowed.", 422)
    if allow_private:
        return url
    host = parts.hostname
    if host.lower() == "localhost" or host.lower().endswith((".local", ".internal")):
        raise AppError(code, "Local or private addresses are blocked for source URLs.", 422)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, parts.port or 443, 0, socket.SOCK_STREAM)
    except socket.gaierror:
        raise AppError("source_unavailable", f"Could not resolve host '{host}'.", 502)
    if not infos or not all(_ip_ok(i[4][0]) for i in infos):
        raise AppError(code, "Local or private addresses are blocked for source URLs.", 422)
    return url
