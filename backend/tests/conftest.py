import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="wpia-test-"))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest
from PIL import Image, ImageDraw


def make_image(w=900, h=900, shape="rect", color=(200, 30, 30), bg=(255, 255, 255), noise=False) -> Image.Image:
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    if shape == "rect":
        d.rectangle([w * 0.3, h * 0.2, w * 0.7, h * 0.8], fill=color)
    elif shape == "wide":
        d.rectangle([w * 0.1, h * 0.4, w * 0.9, h * 0.6], fill=color)
    elif shape == "circle":
        d.ellipse([w * 0.2, h * 0.2, w * 0.8, h * 0.8], fill=color)
    if noise:
        arr = np.asarray(im).astype(np.int16) + np.random.randint(-6, 6, (h, w, 3))
        im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    return im


def to_bytes(im: Image.Image, fmt="JPEG") -> bytes:
    b = io.BytesIO()
    im.save(b, fmt)
    return b.getvalue()


@pytest.fixture
def img_factory():
    return make_image
