import numpy as np
from PIL import Image

from app.services.image_processing_service import HashItem, ImageProcessor
from tests.conftest import make_image, to_bytes

P = ImageProcessor()


def test_validate_accepts_good_image():
    r = P.validate_image(to_bytes(make_image(900, 900)), "https://x.com/phone.jpg")
    assert r.ok and r.ext == "jpg"


def test_validate_rejects_small_banner_corrupt_logo_blank():
    assert not P.validate_image(to_bytes(make_image(200, 200))).ok
    assert "Banner" in P.validate_image(to_bytes(make_image(2400, 600))).reason
    assert not P.validate_image(b"not an image").ok
    assert not P.validate_image(to_bytes(make_image(900, 900)), "https://x.com/site-logo.jpg").ok
    assert not P.validate_image(to_bytes(Image.new("RGB", (900, 900), (255, 255, 255)))).ok


def _hash(path, im):
    im.save(path)
    return P.calculate_hash(path)


def test_dedupe_keeps_highest_resolution(tmp_path):
    big = make_image(1200, 1200, noise=True)
    small = big.resize((700, 700))
    other = make_image(900, 900, shape="circle", color=(10, 10, 200))
    items = [
        HashItem(1, _hash(tmp_path / "a.png", small), 700 * 700, 1000),
        HashItem(2, _hash(tmp_path / "b.png", big), 1200 * 1200, 5000),
        HashItem(3, _hash(tmp_path / "c.png", other), 900 * 900, 2000),
    ]
    kept, dups = P.remove_duplicates(items)
    assert dups == {1: 2}
    assert set(kept) == {2, 3}


def test_crop_keeps_product_and_squares_without_distortion():
    im = make_image(1600, 900, shape="rect")
    out = P.crop_image(im)
    assert out.width == out.height
    arr = np.asarray(out.convert("RGB")).astype(int)
    red = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 80)
    # all red pixels from the original product must remain
    orig = np.asarray(im).astype(int)
    assert red.sum() >= 0.98 * ((orig[:, :, 0] > 150) & (orig[:, :, 1] < 80)).sum()


def test_crop_rejects_bad_claude_box():
    im = make_image(900, 900)
    bad = {"x": 0.45, "y": 0.45, "w": 0.1, "h": 0.1}  # would cut the product
    out = P.crop_image(im, bad)
    assert out.width > 400


def test_crop_wide_product_pads_instead_of_cutting():
    im = make_image(1000, 600, shape="wide")
    out = P.crop_image(im)
    assert out.width == out.height


def test_adjust_does_not_shift_hue():
    im = make_image(600, 600, color=(180, 40, 40))
    out = P.adjust_image(im)
    r, g, b = np.asarray(out).reshape(-1, 3)[600 * 300 + 300]
    assert r > g and r > b


def test_optimize_and_quality(tmp_path):
    im = make_image(3000, 3000, noise=True)
    dest = P.optimize_image(im, tmp_path / "x")
    rep = P.check_quality(dest)
    assert rep.ok and (rep.width, rep.height) == (240, 240) and dest.suffix == ".jpg"
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"junk")
    assert not P.check_quality(bad).ok
    wrong = tmp_path / "wrong.jpg"
    make_image(300, 300).save(wrong)
    assert not P.check_quality(wrong).ok  # not 240x240


import pytest


@pytest.mark.parametrize("fmt,suffix,pil", [("jpg", ".jpg", "JPEG"), ("jpeg", ".jpeg", "JPEG"), ("png", ".png", "PNG"),
                                            ("webp", ".webp", "WEBP"), ("gif", ".gif", "GIF"), ("bmp", ".bmp", "BMP"),
                                            ("tiff", ".tiff", "TIFF")])
def test_every_output_format_is_240(tmp_path, fmt, suffix, pil):
    dest = P.optimize_image(make_image(1600, 900, shape="wide"), tmp_path / "y", fmt)  # non-square input gets padded
    assert dest.suffix == suffix
    with Image.open(dest) as im:
        assert im.format == pil and im.size == (240, 240)
    assert P.check_quality(dest).ok


def test_original_format_helper():
    assert P.format_from_path("a/b/img_001.png") == "png"
    assert P.format_from_path("a/b/img_001.webp") == "webp"
    assert P.format_from_path("a/b/img_001.xyz") == "jpg"