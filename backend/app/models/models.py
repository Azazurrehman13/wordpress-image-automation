from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.db import Base


def _now() -> datetime:
    return datetime.utcnow()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    product_name: Mapped[str] = mapped_column(String(300))
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_product_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # product on destination site
    original_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # image source from description
    destination_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(10), default="update")  # update | create
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    progress: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    verification: Mapped[list] = mapped_column(JSON, default=list)
    result_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    images: Mapped[list["Image"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Image.id"
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text)
    processed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="valid")  # valid | duplicate | rejected
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    wordpress_media_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phash: Mapped[str | None] = mapped_column(String(80), nullable=True)

    job: Mapped[Job] = relationship(back_populates="images")


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[str] = mapped_column(Text)
