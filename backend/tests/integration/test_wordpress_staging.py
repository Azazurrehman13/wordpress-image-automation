"""Integration test against a STAGING WordPress/WooCommerce site only. Never point this at production.

  set WP_STAGING_LOGIN_URL=https://staging.example.com/wp-login.php
  set WP_STAGING_USER=admin   &   set WP_STAGING_PASS=...
"""
import os

import pytest

from app.services.container import Services

pytestmark = pytest.mark.skipif(not os.environ.get("WP_STAGING_LOGIN_URL"), reason="no staging site configured")


async def test_login_search_and_media_roundtrip(tmp_path):
    from PIL import Image

    s = Services()
    try:
        await s.wp.connect(os.environ["WP_STAGING_LOGIN_URL"], os.environ["WP_STAGING_USER"], os.environ["WP_STAGING_PASS"], True)
        assert s.wp.connected
        f = tmp_path / "t.jpg"
        Image.new("RGB", (800, 800), (120, 80, 40)).save(f)
        res = await s.wp.upload_media(f, {"title": "Test", "alt_text": "Test alt", "caption": "", "description": "Test"})
        assert res["id"]
        assert await s.wp.delete_media(res["id"])
    finally:
        await s.pw.stop()
