"""Chooses the 4-5 feature images and the ONE best image. Claude decides; Python enforces the rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.ai import ImageAnalysis
from app.services.claude_service import ClaudeService, ImageInput
from app.utils.errors import AppError

POOL_SIZE = 12
MIN_COMPOSITE = 60.0


@dataclass
class SelectionCandidate:
    image_id: int
    path: Path
    analysis: ImageAnalysis


@dataclass
class SelectionOutcome:
    ordered_ids: list[int]  # best first, then gallery in AI-selected order
    best_id: int
    warnings: list[str] = field(default_factory=list)


def eligible(cands: list[SelectionCandidate]) -> list[SelectionCandidate]:
    good = [c for c in cands if c.analysis.is_product_image and c.analysis.is_same_product
            and (c.analysis.recommended or c.analysis.composite >= MIN_COMPOSITE)]
    return sorted(good, key=lambda c: c.analysis.composite, reverse=True)


def validate_selection(ids: list[int], pool_ids: list[int], scores: dict[int, float], types: dict[int, str],
                       target: int = 5, minimum: int = 4) -> tuple[list[int], list[str]]:
    """Drop unknown/duplicate ids, cap at 5, then top up with distinct views if Claude chose too few."""
    warnings: list[str] = []
    seen: list[int] = []
    for i in ids:
        if i in pool_ids and i not in seen:
            seen.append(i)
    seen = seen[:target]
    need = min(minimum, len(pool_ids))
    if len(seen) < need:
        used_types = {types[i] for i in seen}
        rest = sorted((i for i in pool_ids if i not in seen), key=lambda i: scores[i], reverse=True)
        for i in [r for r in rest if types[r] not in used_types] + rest:
            if len(seen) >= need:
                break
            if i not in seen:
                seen.append(i)
                used_types.add(types[i])
        warnings.append("Claude selected fewer images than expected; the selection was topped up with the next best distinct views.")
    return seen, warnings


class ImageSelectionService:
    def __init__(self, claude: ClaudeService) -> None:
        self.claude = claude

    async def select(self, product_name: str, cands: list[SelectionCandidate]) -> SelectionOutcome:
        pool = eligible(cands)[:POOL_SIZE]
        if not pool:
            raise AppError("no_product_images", "Claude found no usable product images among the candidates.", 422)
        by_id = {c.image_id: c for c in pool}
        analyses = {c.image_id: c.analysis for c in pool}
        scores = {i: a.composite for i, a in analyses.items()}
        types = {i: a.image_type for i, a in analyses.items()}
        inputs = [ImageInput(c.image_id, c.path) for c in pool]
        warnings: list[str] = []

        if len(pool) <= 4:
            chosen = [c.image_id for c in pool]
            if len(pool) < 4:
                warnings.append(f"Only {len(pool)} high-quality image(s) were available; no images were invented.")
        else:
            sel = await self.claude.select_feature_images(inputs, analyses, product_name)
            chosen, w = validate_selection(sel.selected_image_ids, list(by_id), scores, types)
            warnings += w

        if len(chosen) == 1:
            best = chosen[0]
        else:
            res = await self.claude.select_best_feature_image([ImageInput(i, by_id[i].path) for i in chosen], product_name)
            best = res.best_image_id if res.best_image_id in chosen else max(chosen, key=lambda i: scores[i])
            if res.best_image_id not in chosen:
                warnings.append("Claude's best-image choice was not in the selection; the highest scoring image was used.")
        ordered = [best] + [i for i in chosen if i != best]
        return SelectionOutcome(ordered, best, warnings)
