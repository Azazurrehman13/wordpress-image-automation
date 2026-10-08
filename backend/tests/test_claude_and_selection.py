import pytest
from pydantic import ValidationError

from app.schemas.ai import AnalysisBatch, ImageAnalysis, ImageMetadata
from app.services.claude_service import ClaudeService, extract_json
from app.services.image_selection_service import (
    ImageSelectionService, SelectionCandidate, eligible, validate_selection,
)

GOOD = {
    "image_id": 1, "is_product_image": True, "product_visibility_score": 95, "quality_score": 91,
    "composition_score": 90, "feature_visibility_score": 94, "ecommerce_score": 96, "image_type": "Front View",
    "visible_features": ["front panel"], "recommended": True,
}


def test_analysis_schema_valid_and_normalised():
    a = ImageAnalysis(**GOOD)
    assert a.image_type == "front_view" and a.composite > 90


def test_analysis_schema_rejects_out_of_range():
    with pytest.raises(ValidationError):
        ImageAnalysis(**{**GOOD, "quality_score": 140})
    with pytest.raises(ValidationError):
        ImageAnalysis(**{**GOOD, "crop_box": {"x": 0.5, "y": 0, "w": 0, "h": 1}})


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 2} thanks') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("no json")


def test_batch_requires_results():
    with pytest.raises(ValidationError):
        AnalysisBatch.model_validate({"nothing": []})


def test_metadata_sanitised_of_urls_and_domains():
    md = ImageMetadata(title="Phone from https://src.com/p", alt_text="Phone, via shop.example.com", caption="", description="Front view.")
    out = ClaudeService.sanitize_metadata(md, "Galaxy A15", "front_view", banned=["example"])
    assert "http" not in out.title and "shop.example.com" not in out.alt_text
    assert out.caption == "" or "http" not in out.caption


def _cand(i, t, score=80, product=True, rec=True):
    a = ImageAnalysis(**{**GOOD, "image_id": i, "image_type": t, "is_product_image": product, "recommended": rec,
                         "quality_score": score, "product_visibility_score": score, "composition_score": score,
                         "feature_visibility_score": score, "ecommerce_score": score})
    from pathlib import Path
    return SelectionCandidate(i, Path(f"/tmp/{i}.jpg"), a)


def test_eligible_filters_non_products_and_low_scores():
    c = [_cand(1, "front"), _cand(2, "logo", product=False), _cand(3, "x", score=30, rec=False)]
    assert [x.image_id for x in eligible(c)] == [1]


def test_validate_selection_caps_dedupes_and_tops_up():
    pool = [1, 2, 3, 4, 5, 6]
    scores = {i: 100 - i for i in pool}
    types = {1: "front", 2: "front", 3: "side", 4: "back", 5: "detail", 6: "front"}
    ids, w = validate_selection([1, 1, 99, 2], pool, scores, types)
    assert len(ids) == 4 and len(set(ids)) == 4 and not w == []
    assert {"side", "back"} & {types[i] for i in ids}  # topped up with distinct views first
    ids, _ = validate_selection([1, 2, 3, 4, 5, 6], pool, scores, types)
    assert len(ids) == 5


class FakeClaude:
    def __init__(self, selected, best):
        self.selected, self.best = selected, best

    async def select_feature_images(self, inputs, analyses, name):
        from app.schemas.ai import FeatureSelection
        return FeatureSelection(selected_image_ids=self.selected)

    async def select_best_feature_image(self, inputs, name):
        from app.schemas.ai import BestSelection
        return BestSelection(best_image_id=self.best)


async def test_best_image_is_first_and_gallery_keeps_ai_order():
    cands = [_cand(i, t) for i, t in enumerate(["front", "side", "back", "detail", "top", "front"], 1)]
    out = await ImageSelectionService(FakeClaude([3, 1, 2, 4, 5], best=2)).select("Phone", cands)
    assert out.best_id == 2
    assert out.ordered_ids == [2, 3, 1, 4, 5]


async def test_best_not_in_selection_falls_back_to_top_score():
    cands = [_cand(i, t, score=90 - i) for i, t in enumerate(["front", "side", "back", "detail", "top"], 1)]
    out = await ImageSelectionService(FakeClaude([1, 2, 3, 4], best=99)).select("Phone", cands)
    assert out.ordered_ids[0] == 1 and out.warnings


async def test_fewer_than_four_images_are_not_invented():
    cands = [_cand(1, "front"), _cand(2, "side")]
    out = await ImageSelectionService(FakeClaude([], best=2)).select("Phone", cands)
    assert set(out.ordered_ids) == {1, 2} and out.warnings
