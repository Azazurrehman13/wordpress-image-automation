from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import cfg

_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Anything registered here is masked if it ever reaches a log line."""
    if value and len(value) >= 4:
        _SECRETS.add(value)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in _SECRETS:
            if s in msg:
                msg = msg.replace(s, "***")
        if cfg.anthropic_api_key and cfg.anthropic_api_key in msg:
            msg = msg.replace(cfg.anthropic_api_key, "***")
        record.msg, record.args = msg, ()
        return True


def setup_logging() -> None:
    cfg.ensure_dirs()
    root = logging.getLogger()
    if any(getattr(h, "_wpia", False) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(cfg.logs_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        h._wpia = True  # type: ignore[attr-defined]
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def job_log(job_id: str, message: str) -> None:
    from datetime import datetime

    d = cfg.jobs_dir / job_id / "logs"
    d.mkdir(parents=True, exist_ok=True)
    for s in _SECRETS:
        message = message.replace(s, "***")
    with open(d / "job.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
