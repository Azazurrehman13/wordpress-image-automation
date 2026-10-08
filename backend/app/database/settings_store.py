"""Runtime settings: env defaults, overridable from the UI and stored in SQLite."""
from __future__ import annotations

from app.config import cfg
from app.database.db import session_scope
from app.models.models import Setting

DEFAULTS = {
    "headless": cfg.headless,
    "auto_publish": cfg.auto_publish,
    "match_threshold": cfg.match_threshold,
    "strip_source_from_description": cfg.strip_source_from_description,
}


def _cast(key: str, raw: str):
    d = DEFAULTS[key]
    if isinstance(d, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return float(raw)


def get_all() -> dict:
    out = dict(DEFAULTS)
    with session_scope() as s:
        for row in s.query(Setting).all():
            if row.key in DEFAULTS:
                try:
                    out[row.key] = _cast(row.key, row.value)
                except ValueError:
                    pass
    return out


def get(key: str):
    return get_all()[key]


def set_many(values: dict) -> dict:
    with session_scope() as s:
        for k, v in values.items():
            if k not in DEFAULTS or v is None:
                continue
            row = s.query(Setting).filter_by(key=k).first()
            sv = str(v).lower() if isinstance(v, bool) else str(v)
            if row:
                row.value = sv
            else:
                s.add(Setting(key=k, value=sv))
    return get_all()
