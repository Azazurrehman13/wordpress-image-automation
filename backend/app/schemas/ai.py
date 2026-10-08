"""Strict schemas for everything Claude returns. Free-form output is never trusted."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CropBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class ImageAnalysis(BaseModel):
    image_id: int
    is_product_image: bool
    is_same_product: bool = True  # False only when the image clearly shows a DIFFERENT product/model
    product_visibility_score: float = Field(ge=0, le=100)
    quality_score: float = Field(ge=0, le=100)
    composition_score: float = Field(ge=0, le=100)
    feature_visibility_score: float = Field(ge=0, le=100)
    ecommerce_score: float = Field(ge=0, le=100)
    image_type: str = "other"
    visible_features: list[str] = Field(default_factory=list)
    recommended: bool = False
    crop_box: CropBox | None = None

    @field_validator("image_type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return (v or "other").strip().lower().replace(" ", "_").replace("-", "_")[:40] or "other"

    @property
    def composite(self) -> float:
        return round(
            0.25 * self.product_visibility_score
            + 0.25 * self.quality_score
            + 0.15 * self.composition_score
            + 0.15 * self.feature_visibility_score
            + 0.20 * self.ecommerce_score,
            1,
        )


class AnalysisBatch(BaseModel):
    results: list[ImageAnalysis]


class SameProductCheck(BaseModel):
    """Claude's cross-check of ALL candidates against each other: ids that show a different product."""
    different_product_ids: list[int] = Field(default_factory=list)
    reference_image_id: int | None = None


class FeatureSelection(BaseModel):
    selected_image_ids: list[int] = Field(min_length=1, max_length=8)
    coverage_notes: str = ""


class BestSelection(BaseModel):
    best_image_id: int
    reason: str = ""


class ImageMetadata(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    alt_text: str = Field(min_length=1, max_length=160)
    caption: str = Field(default="", max_length=200)
    description: str = Field(min_length=1, max_length=500)


class AltTextItem(BaseModel):
    image_id: int
    alt_text: str = Field(min_length=1, max_length=200)


class AltTextBatch(BaseModel):
    items: list[AltTextItem]
