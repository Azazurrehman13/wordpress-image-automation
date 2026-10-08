from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ConnectRequest(BaseModel):
    login_url: str = Field(min_length=8, max_length=500)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr
    headless: bool | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class InspectRequest(BaseModel):
    product_id: int | None = None
    product_url: str | None = None


class CreateJobRequest(BaseModel):
    product_name: str = Field(min_length=2, max_length=300)
    product_id: int | None = None
    product_url: str | None = None
    mode: str = Field(default="update", pattern="^(update|create)$")
    original_source_url: str | None = None


class BatchProduct(BaseModel):
    """One search result handed to "Use all One by One". Extra fields (score...) are ignored."""
    id: int | None = None
    name: str = Field(default="", max_length=300)
    permalink: str = Field(default="", max_length=1000)
    status: str = Field(default="", max_length=40)


class BatchRequest(BaseModel):
    products: list[BatchProduct] = Field(min_length=1, max_length=50)


class SelectionUpdate(BaseModel):
    image_ids: list[int] = Field(min_length=1, max_length=5)
    best_image_id: int


class SettingsUpdate(BaseModel):
    auto_publish: bool | None = None
    headless: bool | None = None
    match_threshold: float | None = Field(default=None, ge=1, le=100)
    strip_source_from_description: bool | None = None


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_url: str | None = None
    state: str
    rejected_reason: str | None = None
    selected: bool
    is_featured: bool
    position: int | None = None
    ai_score: float | None = None
    analysis: dict[str, Any] | None = None
    title: str | None = None
    alt_text: str | None = None
    caption: str | None = None
    description: str | None = None
    wordpress_media_id: int | None = None
    width: int | None = None
    height: int | None = None
    source_preview: str = ""
    processed_preview: str | None = None


class JobOut(BaseModel):
    id: str
    product_name: str
    product_id: int | None
    source_product_url: str | None
    original_source_url: str | None
    destination_site: str | None
    mode: str
    status: str
    error_code: str | None
    error_message: str | None
    progress: list[dict[str, Any]]
    warnings: list[str]
    verification: list[dict[str, Any]]
    result_url: str | None
    created_at: datetime
    completed_at: datetime | None
    images: list[ImageOut]