"""Runs the REAL job orchestrator end to end against a local fake source website,
a fake WordPress and a fake Claude. Verifies workflow order, image order and cleanup."""
import functools
import http.server
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.config import cfg
from app.database.db import init_db
from app.schemas.ai import ImageAnalysis
from app.services.container import Services
from app.services.wordpress_service import ProductDescription

DESCRIPTION = ('<p>Great phone.</p><p>Original image: '
               '<a href="http://127.0.0.1:{port}/product/galaxy-a15">Original Product</a></p>')


def _img(kind: str, size=900) -> Image.Image:
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    if kind == "rect":
        d.rectangle([270, 180, 630, 720], fill=(200, 30, 30))
    elif kind == "circle":
        d.ellipse([180, 180, 720, 720], fill=(20, 40, 200))
    elif kind == "wide":
        d.rectangle([90, 360, 810, 540], fill=(30, 150, 60))
    elif kind == "tri":
        d.polygon([(450, 150), (780, 750), (120, 750)], fill=(220, 160, 20))
    return im


@pytest.fixture
def source_site(tmp_path):
    root = tmp_path / "site"
    (root / "product").mkdir(parents=True)
    (root / "img").mkdir()
    for n, k in enumerate(["rect", "circle", "wide", "tri"], 1):
        _img(k).save(root / "img" / f"phone-{n}-{k}.jpg", quality=92)
    _img("rect").resize((700, 700)).save(root / "img" / "phone-5-rect-copy.jpg")  # near duplicate, lower resolution
    Image.new("RGB", (600, 600), (5, 5, 5)).save(root / "img" / "site-logo.png")
    tags = "".join(f'<img data-large_image="/img/{p.name}">' for p in sorted((root / "img").iterdir()))
    (root / "product" / "galaxy-a15").write_text(f'<html><body><div class="woocommerce-product-gallery">{tags}</div></body></html>')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def svc(monkeypatch, source_site):
    init_db()
    object.__setattr__(cfg, "allow_private_source_urls", True)
    s = Services()
    wp = s.wp
    wp.connected, wp.base_url = True, "https://target-site.com"
    state = {"uploads": [], "update": None, "deleted": [], "desc": DESCRIPTION.format(port=source_site)}

    async def get_desc(product_id=None, product_url=None):
        return ProductDescription(5, "Samsung Galaxy A15", "https://target-site.com/product/a15/", "draft", state["desc"])

    async def upload(path, meta):
        state["uploads"].append((Path(path), meta))
        return {"id": 100 + len(state["uploads"]), "source_url": "x"}

    async def get_product(pid, raw=False):
        return {"id": pid, "description": state["desc"], "short_description": ""}

    async def update(pid, *, name, media_ids, description=None, short_description=None, status=None):
        state["update"] = {"media_ids": media_ids, "description": description}
        return {"id": 5, "permalink": "https://target-site.com/product/a15/", "status": state.get("update_status", "draft")}

    async def publish(pid):
        state["published"] = True
        return {"id": pid, "permalink": "https://target-site.com/product/a15/", "status": "publish"}

    async def verify(pid, ids, forbidden):
        state["forbidden"] = forbidden
        return [{"name": "Product exists", "ok": True, "detail": ""}]

    async def delete(mid):
        state["deleted"].append(mid)
        return True

    async def update_meta(mid, md):
        state.setdefault("meta_updates", []).append((mid, md))

    for n, f in dict(get_product_description=get_desc, upload_media=upload, get_product=get_product,
                     create_or_update_product=update, publish_product=publish, verify_product=verify,
                     delete_media=delete, update_media_metadata=update_meta).items():
        monkeypatch.setattr(wp, n, f)

    types = {"rect": "front_view", "circle": "side_view", "wide": "detail", "tri": "back_view"}

    def kind_of(path) -> str:
        with Image.open(path) as im:
            r, g, b = im.convert("RGB").getpixel((450, 450))
        return "rect" if r > 150 and b < 80 else "circle" if b > 150 else "wide" if g > 120 else "tri"

    async def analyze(images, batch_size=6, product_name=""):
        out = []
        for im in images:
            kind = kind_of(im.path)
            out.append(ImageAnalysis(image_id=im.id, is_product_image=True, product_visibility_score=90,
                                     quality_score=90 if kind == "circle" else 80, composition_score=85,
                                     feature_visibility_score=85, ecommerce_score=88, image_type=types.get(kind, "other"),
                                     recommended=True))
        return out

    async def best(images, name):
        from app.schemas.ai import BestSelection
        pick = next(i for i in images if kind_of(i.path) == "circle")  # not the highest-ordered one
        return BestSelection(best_image_id=pick.id)

    async def meta(image, name, image_type):
        from app.schemas.ai import ImageMetadata
        return ImageMetadata(title=f"{name} {image_type}", alt_text=f"{name} {image_type} view",
                             caption="", description=f"{image_type} of the product, photo from 127.0.0.1 shop")

    async def alts(images, title, view_types, taken=None):
        colours = ["Black", "Silver", "Blue", "Gold", "Red"]
        state["alt_calls"] = state.get("alt_calls", 0) + 1
        return {im.id: f"{title} {colours[n % 5]}" for n, im in enumerate(images)}

    monkeypatch.setattr(s.claude, "generate_alt_texts", alts)
    monkeypatch.setattr(s.claude, "analyze_images", analyze)
    monkeypatch.setattr(s.claude, "select_best_feature_image", best)
    monkeypatch.setattr(s.claude, "generate_image_metadata", meta)
    s.state = state
    yield s
    object.__setattr__(cfg, "allow_private_source_urls", False)


async def test_full_workflow(svc, source_site):
    jid = svc.jobs.create_job("Samsung Galaxy A15", 5, "https://target-site.com/product/a15/", "update", None)
    await svc.jobs.run(jid)
    job = svc.jobs.get(jid)
    assert job.status == "ready_for_review", (job.error_code, job.error_message)
    assert job.original_source_url == f"http://127.0.0.1:{source_site}/product/galaxy-a15"

    steps = {p["key"]: p["status"] for p in job.progress}
    assert all(steps[k] == "done" for k in ["source_url_found", "images_extracted", "duplicates_removed", "claude_analyzed",
                                          "best_selected", "images_cropped", "images_optimized", "metadata_generated",
                                          "images_uploaded", "ready"])
    states = [i.state for i in job.images]
    assert states.count("duplicate") == 1            # the lower-resolution copy
    assert not any("logo" in i.source_url for i in job.images)  # site graphic never downloaded

    sel = sorted([i for i in job.images if i.selected], key=lambda i: i.position)
    assert len(sel) == 4
    assert sel[0].is_featured and "circle" in sel[0].source_url  # AI's best is first / main
    assert all(Path(i.processed_path).exists() and Path(i.processed_path).suffix == ".jpg" for i in sel)
    uploads = svc.state["uploads"]
    main = uploads[0][1]                                      # best (circle) uploaded first
    assert main["title"] == main["caption"] == main["description"] == "Samsung Galaxy A15"
    for _, m in uploads[1:]:                                  # gallery: same title + a few words
        assert m["title"].startswith("Samsung Galaxy A15 - ") and m["title"] == m["caption"] == m["description"]
    assert len({m["title"] for _, m in uploads}) == len(uploads)
    alt_texts = [m["alt_text"] for _, m in uploads]           # alt text: the title with visible words added, all different
    assert len({a.lower() for a in alt_texts}) == len(alt_texts)
    assert all(a.startswith("Samsung Galaxy A15 ") and a != "Samsung Galaxy A15" for a in alt_texts)
    assert all("127.0.0.1" not in (m["description"] or "") for _, m in uploads)  # source host stripped from metadata

    # nothing touched the product before approval
    assert svc.state["update"] is None

    await svc.jobs.publish(jid)
    job = svc.jobs.get(jid)
    assert job.status == "completed", (job.error_code, job.error_message)
    assert svc.state["update"]["media_ids"] == [i.wordpress_media_id for i in sel]  # main first, gallery in order
    assert "127.0.0.1" not in (svc.state["update"]["description"] or "x")   # source link stripped from description
    assert "Great phone." in svc.state["update"]["description"]
    assert "Original image:" in svc.state["update"]["description"]   # only the link is removed, wording stays
    assert any("127.0.0.1" in f for f in svc.state["forbidden"])
    assert job.result_url.endswith("/product/a15/") and svc.state["published"]


async def test_edit_selection_reprocesses_and_cleans_up(svc):
    jid = svc.jobs.create_job("Samsung Galaxy A15", 5, None, "update", None)
    await svc.jobs.run(jid)
    job = svc.jobs.get(jid)
    sel = sorted([i for i in job.images if i.selected], key=lambda i: i.position)
    keep = [sel[2].id, sel[0].id]
    await svc.jobs.apply_selection(jid, keep, keep[0])
    job = svc.jobs.get(jid)
    now = sorted([i for i in job.images if i.selected], key=lambda i: i.position)
    assert [i.id for i in now] == keep and now[0].is_featured
    assert set(svc.state["deleted"]) == {sel[1].wordpress_media_id, sel[3].wordpress_media_id}
    assert svc.state["alt_calls"] == 1                      # kept alt texts are reused, Claude is not asked again
    assert len({i.alt_text for i in now}) == len(now)


async def test_missing_source_url_is_a_clear_error(svc):
    svc.state["desc"] = '<p>No links here, just text. <a href="https://facebook.com/x">fb</a></p>'
    jid = svc.jobs.create_job("Samsung Galaxy A15", 5, None, "update", None)
    await svc.jobs.run(jid)
    job = svc.jobs.get(jid)
    assert job.status == "failed" and job.error_code == "source_url_not_found"
    assert next(p for p in job.progress if p["key"] == "source_url_found")["status"] == "error"
    assert not svc.state["uploads"]


@pytest.mark.parametrize("current,expect_publish_call", [("private", True), ("draft", True), ("pending", True), ("publish", False)])
async def test_product_always_ends_up_public(svc, current, expect_publish_call):
    svc.state["update_status"] = current
    jid = svc.jobs.create_job("Samsung Galaxy A15", 5, "https://target-site.com/product/a15/", "update", None)
    await svc.jobs.run(jid)
    await svc.jobs.publish(jid)
    assert svc.jobs.get(jid).status == "completed"
    assert bool(svc.state.get("published")) is expect_publish_call
