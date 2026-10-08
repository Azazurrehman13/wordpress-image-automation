import asyncio
from app.schemas.ai import ImageAnalysis, SameProductCheck
from app.services.claude_service import ClaudeService, ImageInput
from app.services.image_extraction_service import parse_html
from app.services.image_metadata import build_metadata

BASE = "https://src.com/product/x"

# no recognisable gallery class: the other products sit in a block with an unusual class name
NO_GALLERY = """<html><head><meta property="og:image" content="https://src.com/p/1.jpg"></head><body><main>
  <div class="pdp-media"><img src="/p/1.jpg"><img src="/p/2.jpg"><img data-src="/p/3.jpg"></div>
  <div class="pdp-tiles"><img src="/other/A.jpg"><img src="/other/B.jpg"><img src="/other/C.jpg"></div>
</main></body></html>"""


def test_fallback_scope_is_narrowed_to_the_container_of_the_products_own_image():
    u = [c.url for c in parse_html(NO_GALLERY, BASE)]
    assert {"https://src.com/p/1.jpg", "https://src.com/p/2.jpg", "https://src.com/p/3.jpg"} <= set(u)
    assert not any("/other/" in x for x in u)


def test_without_any_anchor_the_old_behaviour_is_unchanged():
    html = '<main><img src="/p/1.jpg"><img src="/p/2.jpg"></main>'
    assert [c.url for c in parse_html(html, BASE)] == ["https://src.com/p/1.jpg", "https://src.com/p/2.jpg"]


def _a(i):
    return ImageAnalysis(image_id=i, is_product_image=True, product_visibility_score=90, quality_score=90,
                         composition_score=90, feature_visibility_score=90, ecommerce_score=90, recommended=True)


def _run(flagged, ref=None, n=5):
    svc = ClaudeService()

    async def fake_blocks(images, side=None):
        return []

    async def fake_ask(model, blocks, instruction, max_tokens=0):
        return SameProductCheck(different_product_ids=flagged, reference_image_id=ref)

    svc._blocks, svc._ask = fake_blocks, fake_ask
    imgs = [ImageInput(i, None) for i in range(1, n + 1)]
    res = {i: _a(i) for i in range(1, n + 1)}
    asyncio.run(svc._cross_check_same_product(imgs, res, "Galaxy A15"))
    return {i: r.is_same_product for i, r in res.items()}


def test_cross_check_marks_images_of_other_products():
    assert _run([4]) == {1: True, 2: True, 3: True, 4: False, 5: True}


def test_cross_check_ignores_unknown_ids_and_never_flags_the_reference():
    assert _run([99, 2], ref=2) == {i: True for i in range(1, 6)}


def test_cross_check_is_ignored_when_it_flags_most_images():
    assert all(_run([1, 2, 3]).values())


def test_title_is_decoded_and_clean_for_every_image_text():
    md = build_metadata("Tom &amp; Jerry &#8211; <b>Set</b> https://src.com/x", "front_view", True, banned=["src.com"])
    assert md == {k: "Tom & Jerry \u2013 Set" for k in ("title", "alt_text", "caption", "description")}
