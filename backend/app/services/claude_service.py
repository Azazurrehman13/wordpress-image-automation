"""All Claude calls. Every response is parsed as JSON and validated with Pydantic."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import anthropic
from bs4 import BeautifulSoup
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.config import cfg
from app.schemas.ai import AnalysisBatch, BestSelection, FeatureSelection, ImageAnalysis, ImageMetadata, SameProductCheck, AltTextBatch
from app.utils.errors import AppError
from app.utils.text import URL_RE

T = TypeVar("T", bound=BaseModel)

SYSTEM = (
    "You are a precise e-commerce product image analyst. Respond with ONE JSON object only: "
    "no prose, no markdown, no code fences. Judge only what is visible. Never invent specifications."
)


class SourceUrlChoice(BaseModel):
    """Claude's pick among the links found in a product description (an index into the list it was given)."""
    chosen_index: int | None = None
    reason: str = ""


class ProductGroup(BaseModel):
    name: str = ""
    image_ids: list[int] = []


class ProductGrouping(BaseModel):
    """All candidate images grouped by the physical product they show, and which group is THE product being sold."""
    groups: list[ProductGroup] = []
    matching_group: int | None = None
    reason: str = ""


class OddImages(BaseModel):
    different_product_ids: list[int] = []


@dataclass
class ImageInput:
    id: int
    path: Path


def extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            return json.loads(text[s:e + 1])
        raise


def _thumb_b64(path: Path, max_side: int) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def _plain_text(html: str, limit: int = 1500) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)[:limit]


class ClaudeService:
    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if not cfg.anthropic_api_key:
            raise AppError("claude_api_failure", "ANTHROPIC_API_KEY is not set in backend/.env.", 500)
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key, timeout=120)
        return self._client

    async def _blocks(self, images: list[ImageInput], max_side: int | None = None) -> list[dict]:
        side = max_side or cfg.claude_image_max_side
        blocks: list[dict] = []
        for img in images:
            b64 = await asyncio.to_thread(_thumb_b64, img.path, side)
            blocks.append({"type": "text", "text": f"Image id={img.id}:"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        return blocks

    async def _ask(self, model: type[T], blocks: list[dict], instruction: str, max_tokens: int = 3000) -> T:
        content = [*blocks, {"type": "text", "text": instruction}]
        last_err = ""
        for attempt in range(2):
            msg = list(content)
            if attempt:
                msg.append({"type": "text", "text": f"Your previous reply was invalid ({last_err}). Return only valid JSON that matches the schema exactly."})
            try:
                resp = await self.client.messages.create(
                    model=cfg.claude_model, max_tokens=max_tokens, system=SYSTEM,
                    messages=[{"role": "user", "content": msg}],
                )
            except anthropic.APIError as e:
                raise AppError("claude_api_failure", f"Claude API error: {getattr(e, 'message', str(e))}", 502)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            try:
                return model.model_validate(extract_json(text))
            except (ValueError, ValidationError) as e:
                last_err = str(e)[:300]
        raise AppError("invalid_claude_response", f"Claude returned an invalid response twice: {last_err}", 502)

    # ------------------------------------------------------------ source link
    async def choose_source_url(self, description_html: str, product_name: str, links: list[dict]) -> tuple[int | None, str]:
        """Read the product description carefully and decide which link is the ORIGINAL SOURCE of the product's images.
        `links` = [{"index", "url", "link_text", "text_before", "rule_score", "kind"}]. Returns (index or None, reason).
        Claude can only choose from the given links, so a URL can never be invented."""
        text = BeautifulSoup(description_html or "", "html.parser").get_text(" ", strip=True)[:3000]
        instruction = (
            f'Product title: "{product_name}".\n'
            f"Description text of this product (HTML removed):\n{text}\n\n"
            f"Links found inside the description (index, url, link text, text right before the link, rule-based score):\n"
            f"{json.dumps(links, ensure_ascii=False)}\n\n"
            "Decide which ONE link is the ORIGINAL SOURCE of this product's images: the original product page (or image page) "
            "of this same product on the brand's or retailer's website. Read the surrounding wording carefully, for example "
            '"Original image:", "Source:", "Image source:", "Photo from". The URL path or slug usually names the same product. '
            "Ignore social media, legal/policy/contact pages, the store itself, generic home pages, category or search pages, "
            "and links to other products. Prefer a product PAGE over a direct image file. "
            'Return JSON: {"chosen_index":<index or null when no link is clearly the original product source>,"reason":"short"}. '
            "Never invent a link; use only the given indexes."
        )
        res = await self._ask(SourceUrlChoice, [], instruction, 500)
        idx = res.chosen_index
        if idx is not None and not any(l["index"] == idx for l in links):
            idx = None
        return idx, res.reason

    # ------------------------------------------------------------- analysis
    async def analyze_images(self, images: list[ImageInput], batch_size: int = 6, product_name: str = "",
                             description_html: str = "", check_same_product: bool = True) -> list[ImageAnalysis]:
        results: dict[int, ImageAnalysis] = {}
        instruction = (
            'Analyse each image above for use on an e-commerce product page. Return JSON: '
            '{"results":[{"image_id":<int>,"is_product_image":bool,"product_visibility_score":0-100,'
            '"quality_score":0-100,"composition_score":0-100,"feature_visibility_score":0-100,'
            '"ecommerce_score":0-100,"image_type":"front_view|back_view|side_view|three_quarter_view|top_view|'
            'detail|feature_closeup|lifestyle|packaging|accessories|other","visible_features":[short strings],'
            '"recommended":bool,"crop_box":{"x":0-1,"y":0-1,"w":0-1,"h":0-1} or null}]}. '
            "is_product_image is true for ANY photo that clearly shows the product itself: front, back, side, open/interior, "
            "close-up detail views, AND on-model or in-hand lifestyle photos where the product is clearly visible (score those "
            "lifestyle shots lower on ecommerce_score, but they are still product images). "
            "crop_box must keep the WHOLE product with a visible 5-10% safety margin on every applicable side; "
            "never crop so the product touches or nearly touches the frame edge. If the source image already has poor edge framing, "
            "score composition lower and do not recommend it when better-framed images exist. "
            "Logos, banners, icons, size charts, shipping/returns graphics and unrelated images are not product images. One result per image id."
        )
        if product_name and check_same_product:
            instruction += (
                f' The product for this page is: "{product_name}". Add "is_same_product":bool to every result: true when the image '
                "shows this product (any angle, close-up, feature detail, its packaging or accessories); false ONLY when it clearly "
                "shows a different product or model (for example a related, recommended or competing item)."
            )
        for i in range(0, len(images), batch_size):
            batch = images[i:i + batch_size]
            parsed = await self._ask(AnalysisBatch, await self._blocks(batch), instruction)
            wanted = {b.id for b in batch}
            for r in parsed.results:
                if r.image_id in wanted:
                    results[r.image_id] = r
            missing = wanted - results.keys()
            if missing:
                raise AppError("invalid_claude_response", f"Claude skipped images {sorted(missing)}.", 502)
        if product_name and check_same_product and len(images) >= 1:
            await self._group_by_product(images, results, product_name, description_html)
        return [results[img.id] for img in images]

    async def _group_by_product(self, images: list[ImageInput], results: dict[int, ImageAnalysis],
                                product_name: str, description_html: str) -> None:
        """Per-batch analysis never sees the other batches, and a source page often shows OTHER products (related items, other
        models) next to the real one. Look at every candidate together, group them by the physical product they show, and
        keep only the group that matches the title AND the description. Everything else is marked as a different product."""
        text = _plain_text(description_html)
        instruction = (
            f'All images above were collected from ONE web page that sells: "{product_name}".\n'
            f'Product description: {text or "(not available)"}\n\n'
            "The page may ALSO contain photos of OTHER products (related or recommended items, other models, other colours). "
            "Group the images by the exact same physical product: same design, shape, closure and hardware, proportions, material "
            "and colour. Different models are different groups even when they share a brand and style (for example a zip-top pouch "
            "and a flat folded clutch are different products; a grey item and a black item are different products). "
            "Different angles or close-ups of ONE product belong in ONE group. "
            "Then choose the group that matches the product title AND the description (colour, shape, closure, features). "
            'Return JSON: {"groups":[{"name":"short description of the product","image_ids":[ids]}],'
            '"matching_group":<index into groups, or null if unsure>,"reason":"short"}. '
            "Every given id must appear in exactly one group. Use only the given ids."
        )
        grouping = await self._ask(ProductGrouping, await self._blocks(images, 640), instruction, 1500)
        valid = {i.id for i in images}
        idx = grouping.matching_group
        if idx is None or not (0 <= idx < len(grouping.groups)):
            return
        keep = set(grouping.groups[idx].image_ids) & valid
        if not keep:
            return
        for i in valid - keep:
            results[i] = results[i].model_copy(update={"is_same_product": False})

    async def find_odd_images(self, images: list[ImageInput], product_name: str, description_html: str = "") -> list[int]:
        """Last safety net: the images chosen for the listing must all show ONE product. Returns the ids of any image
        that shows a different product, model, shape, closure or colour than the product being sold."""
        text = _plain_text(description_html)
        instruction = (
            f'These images are meant to be the gallery of ONE product: "{product_name}".\n'
            f'Product description: {text or "(not available)"}\n\n'
            "Find every image that shows a DIFFERENT product from the one described: a different model, shape, closure, hardware "
            "or colour (for example a zip-top pouch among flat folded clutches). Different angles or close-ups of the same product "
            'are fine. Return JSON: {"different_product_ids":[ids]}. Use an empty list when all images show the same product. '
            "Use only the given ids."
        )
        res = await self._ask(OddImages, await self._blocks(images, 768), instruction, 400)
        valid = {i.id for i in images}
        return [i for i in res.different_product_ids if i in valid]

    async def select_feature_images(self, images: list[ImageInput], analyses: dict[int, ImageAnalysis],
                                    product_name: str, count: int = 5) -> FeatureSelection:
        summary = [{"id": i.id, "type": analyses[i.id].image_type, "score": analyses[i.id].composite,
                    "features": analyses[i.id].visible_features} for i in images]
        instruction = (
            f'Product: "{product_name}". Candidates (with earlier analysis): {json.dumps(summary)}\n'
            f"Choose EXACTLY {min(5, len(images))} images when at least 5 candidates exist; otherwise choose all available candidates "
            "but never fewer than 4 when 4 or more valid candidates exist. The final product page must contain 4-5 images. "
            "Choose the strongest visually attractive images with useful coverage: best overall/front view, side or three-quarter view, "
            "rear/back view when genuinely useful, an important feature/detail, and another distinct useful angle. "
            "Do NOT treat two images as duplicates merely because they show the same product or have a similar composition. "
            "Only exclude an image as redundant when it is essentially the same photograph/frame with no meaningful visual difference. "
            "Prefer distinct camera angles, visible product areas and feature coverage. Reject images where the product is cut off, touches the "
            "edge, is badly cropped, poorly centered, blurry, heavily obstructed, or visually unattractive when a better candidate exists. "
            "Every chosen image must show the SAME single product. "
            "Use only the given ids and return them in best-to-worst order. "
            'Return JSON: {"selected_image_ids":[ids in order],"coverage_notes":"short"}'
        )
        return await self._ask(FeatureSelection, await self._blocks(images, 768), instruction, 1000)

    async def select_best_feature_image(self, images: list[ImageInput], product_name: str) -> BestSelection:
        instruction = (
            f'Product: "{product_name}". From these selected images choose exactly ONE as the MAIN product image: '
            "the strongest first impression: fully visible product, clean composition, centered framing, sufficient margin from all frame edges, "
            "good resolution, clean background, useful feature visibility, and an attractive e-commerce presentation. Never choose an image "
            "where the product is cut off or touches the frame edge when another well-framed image is available. "
            'Return JSON: {"best_image_id":<id>,"reason":"short"}'
        )
        return await self._ask(BestSelection, await self._blocks(images, 768), instruction, 600)

    async def generate_image_metadata(self, image: ImageInput, product_name: str, image_type: str) -> ImageMetadata:
        instruction = (
            f'Product title: "{product_name}". View type: {image_type}. Write WordPress media metadata describing ONLY what is visible. '
            "Do not invent specifications, claims or brand facts. No URLs, no website names. "
            "The title MUST be exactly the Product title provided above. Do not rewrite, shorten, expand, reorder or modify it. "
            "The alt_text MUST also be exactly the Product title provided above. "
            'Return JSON: {"title":"exact product title","alt_text":"exact product title","caption":"<=120 chars","description":"1-2 visual sentences"}'
        )
        md = await self._ask(ImageMetadata, await self._blocks([image], 768), instruction, 600)
        return self.sanitize_metadata(md, product_name, image_type)

    async def generate_alt_texts(
        self,
        images: list[ImageInput],
        product_title: str,
        view_types: dict[int, str],
        taken: list[str] | None = None,
    ) -> dict[int, str]:
        """Return the original draft product title exactly as supplied for every image."""
        if not product_title:
            raise AppError(
                "invalid_product_title",
                "Product title is empty. Cannot generate alt text.",
                400,
            )

        # Intentionally do not call Claude here. This guarantees the original
        # draft title can never be rewritten, reordered, shortened, expanded,
        # or supplemented with AI-generated words.
        return {image.id: product_title for image in images}

    @staticmethod
    def sanitize_metadata(md: ImageMetadata, product_name: str, image_type: str, banned: list[str] | None = None) -> ImageMetadata:
        def clean(s: str) -> str:
            s = URL_RE.sub("", s)
            s = re.sub(r"\b[\w-]+\.(?:com|net|org|co|io|shop|store|pk|uk)\b", "", s, flags=re.I)
            for b in banned or []:
                s = re.sub(re.escape(b), "", s, flags=re.I)
            return re.sub(r"\s{2,}", " ", s).strip(" -–—,;:")

        label = image_type.replace("_", " ").title()
        data = {k: clean(getattr(md, k)) for k in ("title", "alt_text", "caption", "description")}
        # The draft product title is authoritative. Never allow AI or sanitization
        # to rewrite the title or alt text.
        data["title"] = product_name
        data["alt_text"] = product_name
        data["description"] = data["description"] or f"{label} of {product_name}."
        return ImageMetadata(**data)