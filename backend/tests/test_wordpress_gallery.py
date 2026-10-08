"""WordPress behaviour with the REST layer faked (no real site needed)."""
import pytest

from app.services.playwright_service import PlaywrightService
from app.services.wordpress_service import RestResponse, WordPressAutomation
from app.utils.errors import AppError


class FakeWP(WordPressAutomation):
    def __init__(self):
        super().__init__(PlaywrightService())
        self.base_url = "https://target-site.com"
        self.calls = []
        self.fail_meta = False

    async def rest(self, method, route, *, params=None, data=None, headers=None, _retry=True):
        self.calls.append((method, route, data if not isinstance(data, bytes) else "<bytes>"))
        if route == "wp/v2/media" and method == "POST":
            return RestResponse(201, {"id": 77, "source_url": "https://target-site.com/u/a.jpg"})
        if route.startswith("wp/v2/media/") and method == "POST":
            return RestResponse(500 if self.fail_meta else 200, {"message": "boom"} if self.fail_meta else {})
        if route.startswith("wp/v2/media/") and method == "DELETE":
            return RestResponse(200, {})
        if route.startswith("wc/v3/products"):
            return RestResponse(200, {"id": 5, "images": [{"id": i["id"]} for i in data["images"]]})
        return RestResponse(404, None)


async def test_main_image_first_then_gallery_in_order():
    wp = FakeWP()
    await wp.create_or_update_product(5, name="X", media_ids=[30, 10, 20, 40, 50])
    method, route, payload = wp.calls[-1]
    assert (method, route) == ("PUT", "wc/v3/products/5")
    assert [i["id"] for i in payload["images"]] == [30, 10, 20, 40, 50]
    assert set(payload) == {"images"}  # nothing unrelated is touched


async def test_create_listing_is_a_draft():
    wp = FakeWP()
    await wp.create_or_update_product(None, name="New", media_ids=[1, 2])
    method, route, payload = wp.calls[-1]
    assert method == "POST" and payload["status"] == "draft"


async def test_upload_sets_metadata(tmp_path):
    wp = FakeWP()
    f = tmp_path / "a.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    res = await wp.upload_media(f, {"filename": "../bad name.jpg", "title": "T", "alt_text": "A", "caption": "C", "description": "D"})
    assert res["id"] == 77
    assert wp.calls[-1][2] == {"title": "T", "alt_text": "A", "caption": "C", "description": "D"}


async def test_upload_cleans_up_when_metadata_fails(tmp_path):
    wp = FakeWP()
    wp.fail_meta = True
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    with pytest.raises(AppError) as e:
        await wp.upload_media(f, {"title": "T"})
    assert e.value.code == "wordpress_upload_failed"
    assert wp.calls[-1][0] == "DELETE"


def test_base_url_derivation():
    d = WordPressAutomation.derive_base_url
    assert d("https://example.com/wp-login.php") == "https://example.com"
    assert d("https://example.com/shop/wp-admin/") == "https://example.com/shop"
    with pytest.raises(AppError):
        d("example.com/wp-login.php")
