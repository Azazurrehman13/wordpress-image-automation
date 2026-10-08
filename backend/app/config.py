"""Application configuration, loaded from backend/.env (never commit that file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _resolve_db_url(url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        p = Path(raw)
        if not p.is_absolute():
            p = (ROOT_DIR / raw).resolve()
        return prefix + p.as_posix()
    return url


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    claude_model: str
    claude_image_max_side: int
    host: str
    port: int
    database_url: str
    headless: bool
    auto_publish: bool
    match_threshold: float
    strip_source_from_description: bool
    allow_private_source_urls: bool
    min_image_side: int
    min_final_side: int
    max_download_bytes: int
    max_upload_bytes: int
    max_candidates: int
    max_analyze: int
    max_output_side: int
    output_size: int
    jpeg_quality: int
    output_format: str
    search_statuses: tuple
    cors_origins: tuple
    data_dir: Path

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "browser_profile"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.jobs_dir, self.profile_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    data_dir = Path(_str("DATA_DIR") or ROOT_DIR / "data").resolve()
    return Config(
        anthropic_api_key=_str("ANTHROPIC_API_KEY"),
        claude_model=_str("CLAUDE_MODEL", "claude-sonnet-5-5"),
        claude_image_max_side=_int("CLAUDE_IMAGE_MAX_SIDE", 1024),
        host=_str("BACKEND_HOST", "127.0.0.1"),
        port=_int("BACKEND_PORT", 8000),
        database_url=_resolve_db_url(_str("DATABASE_URL", "sqlite:///./data/app.db")),
        headless=_bool("PLAYWRIGHT_HEADLESS", True),
        auto_publish=_bool("AUTO_PUBLISH", False),
        match_threshold=_float("MATCH_THRESHOLD", 85.0),
        strip_source_from_description=_bool("STRIP_SOURCE_FROM_DESCRIPTION", True),
        allow_private_source_urls=_bool("ALLOW_PRIVATE_SOURCE_URLS", False),
        min_image_side=_int("MIN_IMAGE_SIDE", 500),
        min_final_side=_int("MIN_FINAL_SIDE", 400),
        max_download_bytes=_int("MAX_DOWNLOAD_BYTES", 15_000_000),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 2_500_000),
        max_candidates=_int("MAX_CANDIDATES", 30),
        max_analyze=_int("MAX_ANALYZE", 24),
        max_output_side=_int("MAX_OUTPUT_SIDE", 2000),
        output_size=_int("OUTPUT_SIZE", 240),
        jpeg_quality=_int("JPEG_QUALITY", 88),
        output_format=_str("OUTPUT_FORMAT", "jpg").lower(),
        search_statuses=tuple(x.strip().lower() for x in _str("SEARCH_STATUSES", "draft").split(",") if x.strip()) or ("draft",),
        cors_origins=tuple(
            o.strip() for o in _str("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()
        ),
        data_dir=data_dir,
    )


cfg = load_config()