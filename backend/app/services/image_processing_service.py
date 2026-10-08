"""All deterministic, local image work: validation, hashing, dedupe, crop, adjust, optimise."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from app.config import cfg
from app.utils.errors import AppError

Image.MAX_IMAGE_PIXELS = 60_000_000
ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}

# OUTPUT_FORMAT value -> (Pillow format name, file extension).  "original" keeps the source image's own format.
OUTPUT_FORMATS = {
    "jpg": ("JPEG", ".jpg"), "jpeg": ("JPEG", ".jpeg"), "png": ("PNG", ".png"), "webp": ("WEBP", ".webp"),
    "gif": ("GIF", ".gif"), "bmp": ("BMP", ".bmp"), "tiff": ("TIFF", ".tiff"), "tif": ("TIFF", ".tif"),
}


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    width: int = 0
    height: int = 0
    format: str = ""
    ext: str = "jpg"


@dataclass
class ImageHashes:
    ahash: int
    dhash: int
    phash: int

    def to_str(self) -> str:
        return f"{self.phash:016x}:{self.dhash:016x}:{self.ahash:016x}"

    @staticmethod
    def from_str(s: str) -> "ImageHashes":
        p, d, a = (int(x, 16) for x in s.split(":"))
        return ImageHashes(ahash=a, dhash=d, phash=p)


@dataclass
class HashItem:
    id: int
    hashes: ImageHashes
    pixels: int
    size: int


@dataclass
class QualityReport:
    width: int
    height: int
    bytes: int
    sharpness: float
    issues: list[str] = field(default_factory=list)  # hard failures
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _bits(arr: np.ndarray) -> int:
    v = 0
    for bit in arr.flatten():
        v = (v << 1) | int(bit)
    return v


class ImageProcessor:
    # ---------------------------------------------------------------- validate
    def validate_image(self, data: bytes, url: str = "") -> ValidationResult:
        low = url.lower()
        for bad in ("logo", "icon", "sprite", "favicon", "avatar", "placeholder", "pixel", "tracking",
                    "spinner", "loader", "badge", "payment", "gravatar", "blank", "banner"):
            if bad in low.rsplit("/", 1)[-1]:
                return ValidationResult(False, f"Looks like a site graphic ('{bad}' in file name)")
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format or ""
                w, h = im.size
                im.load()
                gray = np.asarray(im.convert("L").resize((64, 64)))
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return ValidationResult(False, "Corrupt or unsupported image")
        if fmt not in ALLOWED_FORMATS:
            return ValidationResult(False, f"Unsupported format {fmt}")
        if min(w, h) < cfg.min_image_side:
            return ValidationResult(False, f"Too small ({w}x{h})", w, h, fmt)
        ratio = w / h
        if ratio > 3.0 or ratio < 1 / 3.0:
            return ValidationResult(False, f"Banner-like aspect ratio ({w}x{h})", w, h, fmt)
        if float(gray.std()) < 2.0:
            return ValidationResult(False, "Blank image", w, h, fmt)
        return ValidationResult(True, "", w, h, fmt, ALLOWED_FORMATS[fmt])

    # ------------------------------------------------------------------ hashes
    def calculate_hash(self, path: str | Path) -> ImageHashes:
        with Image.open(path) as im:
            gray = im.convert("L")
            a = np.asarray(gray.resize((8, 8), Image.LANCZOS), dtype=np.float32)
            ahash = _bits(a > a.mean())
            d = np.asarray(gray.resize((9, 8), Image.LANCZOS), dtype=np.float32)
            dhash = _bits(d[:, 1:] > d[:, :-1])
            p = np.asarray(gray.resize((32, 32), Image.LANCZOS), dtype=np.float32)
            dct = cv2.dct(p)[:8, :8].flatten()
            phash = _bits(dct > np.median(dct[1:]))
        return ImageHashes(ahash, dhash, phash)

    @staticmethod
    def is_near_duplicate(a: ImageHashes, b: ImageHashes) -> bool:
        p, d, av = hamming(a.phash, b.phash), hamming(a.dhash, b.dhash), hamming(a.ahash, b.ahash)
        # pHash alone is unstable on plain white-background product shots, so dHash+aHash can confirm too.
        return p <= 4 or (d <= 6 and av <= 6) or (p <= 8 and d <= 8 and av <= 8)

    def remove_duplicates(self, items: list[HashItem]) -> tuple[list[int], dict[int, int]]:
        """Keep the highest-resolution (then largest file) of each near-duplicate group.
        Returns (kept_ids, {duplicate_id: kept_id})."""
        ordered = sorted(items, key=lambda i: (i.pixels, i.size), reverse=True)
        kept: list[HashItem] = []
        dups: dict[int, int] = {}
        for it in ordered:
            match = next((k for k in kept if self.is_near_duplicate(it.hashes, k.hashes)), None)
            if match:
                dups[it.id] = match.id
            else:
                kept.append(it)
        kept_ids = [i.id for i in items if i.id not in dups]
        return kept_ids, dups

    # -------------------------------------------------------------------- crop
    @staticmethod
    def _foreground(img: Image.Image):
        """Return (bbox, reliable, bg_rgb) in original pixel coordinates."""
        rgb = np.asarray(img.convert("RGB"))
        h, w = rgb.shape[:2]
        scale = min(1.0, 512 / max(h, w))
        small = cv2.resize(rgb, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
        border = np.concatenate([small[0, :], small[-1, :], small[:, 0], small[:, -1]]).astype(np.float32)
        bg = np.median(border, axis=0)
        reliable = float(np.mean(np.std(border, axis=0))) < 18.0
        diff = np.abs(small.astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
        mask = (diff > 45).astype(np.uint8) * 255
        k = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
        ys, xs = np.where(mask > 0)
        full = (0, 0, w, h)
        bgc = tuple(int(c) for c in bg)
        if len(xs) < 0.002 * mask.size:
            return full, False, bgc
        sx, sy = w / small.shape[1], h / small.shape[0]
        box = (int(xs.min() * sx), int(ys.min() * sy), int((xs.max() + 1) * sx), int((ys.max() + 1) * sy))
        return box, reliable, bgc

    @staticmethod
    def _contains(outer, inner, frac: float = 0.95) -> bool:
        ix0, iy0, ix1, iy1 = inner
        area = max(1, (ix1 - ix0) * (iy1 - iy0))
        ox0, oy0, ox1, oy1 = outer
        inter = max(0, min(ix1, ox1) - max(ix0, ox0)) * max(0, min(iy1, oy1) - max(iy0, oy0))
        return inter / area >= frac

    def crop_image(self, img: Image.Image, crop_box: dict | None = None, margin: float = 0.07) -> Image.Image:
        """Crop to the product with margin, then square it without distorting.
        Claude's suggested box is only used if it passes deterministic checks."""
        img = img.convert("RGB")
        w, h = img.size
        fg, reliable, bg = self._foreground(img)
        box = None
        if crop_box:
            cb = (crop_box["x"] * w, crop_box["y"] * h, (crop_box["x"] + crop_box["w"]) * w,
                  (crop_box["y"] + crop_box["h"]) * h)
            big_enough = (cb[2] - cb[0]) * (cb[3] - cb[1]) >= 0.35 * w * h
            if big_enough and (not reliable or self._contains(cb, fg)):
                box = tuple(int(v) for v in cb)
        if box is None and reliable:
            box = fg
        if box is None:
            box = (0, 0, w, h)
        x0, y0, x1, y1 = box
        mx, my = (x1 - x0) * margin, (y1 - y0) * margin
        x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)
        bw, bh = x1 - x0, y1 - y0
        if bw * bh > 0.92 * w * h:
            x0, y0, x1, y1, bw, bh = 0, 0, w, h, w, h
        side = max(bw, bh)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if side <= min(w, h):
            sx0 = min(max(0, cx - side / 2), w - side)
            sy0 = min(max(0, cy - side / 2), h - side)
            return img.crop((int(sx0), int(sy0), int(sx0 + side), int(sy0 + side)))
        cropped = img.crop((int(x0), int(y0), int(x1), int(y1)))
        if reliable and cropped.width != cropped.height:
            s = max(cropped.size)
            canvas = Image.new("RGB", (s, s), bg)
            canvas.paste(cropped, ((s - cropped.width) // 2, (s - cropped.height) // 2))
            return canvas
        return cropped

    # ------------------------------------------------------------------ adjust
    def adjust_image(self, img: Image.Image) -> Image.Image:
        """Conservative: luminance-only tweaks so product colours are never shifted."""
        img = img.convert("RGB")
        ycc = img.convert("YCbCr")
        y, cb, cr = ycc.split()
        mean = ImageStat.Stat(y).mean[0]
        y2 = ImageOps.autocontrast(y, cutoff=0.3)
        y = Image.blend(y, y2, 0.5)
        if mean < 100:
            factor = min(1.15, 110 / max(mean, 1))
            y = y.point(lambda v: min(255, int(v * factor)))
        out = Image.merge("YCbCr", (y, cb, cr)).convert("RGB")
        gray = np.asarray(out.convert("L").resize((min(out.width, 800), min(out.height, 800))))
        soft = cv2.Laplacian(gray, cv2.CV_64F).var() < 150
        return out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60 if soft else 25, threshold=3))

    # ---------------------------------------------------------------- optimise
    @staticmethod
    def _edge_color(img: Image.Image) -> tuple:
        """Median colour of the image border, used as padding so non-square images are never stretched."""
        a = np.asarray(img.convert("RGB"))
        border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
        return tuple(int(v) for v in np.median(border, axis=0))

    def optimize_image(self, img: Image.Image, dest: Path, fmt: str | None = None) -> Path:
        """Resize to exactly cfg.output_size x cfg.output_size (no distortion) and save in the chosen format.
        fmt: one of OUTPUT_FORMATS, or "original"; defaults to cfg.output_format."""
        img = img.convert("RGB")
        size = cfg.output_size
        if img.size != (size, size):
            # contain + pad keeps the whole product visible; crop_image() already squares most images
            img = ImageOps.pad(img, (size, size), method=Image.LANCZOS, color=self._edge_color(img))
            img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=40, threshold=2))  # restore detail lost by downscaling
        key = (fmt or cfg.output_format or "jpg").lower().lstrip(".")
        if key not in OUTPUT_FORMATS:
            key = "jpg"
        pil_fmt, ext = OUTPUT_FORMATS[key]
        dest = dest.with_suffix(ext)
        q = cfg.jpeg_quality
        while True:
            buf = io.BytesIO()
            if pil_fmt == "JPEG":
                img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
            elif pil_fmt == "WEBP":
                img.save(buf, "WEBP", quality=q, method=6)
            elif pil_fmt == "PNG":
                img.save(buf, "PNG", optimize=True)
            elif pil_fmt == "GIF":
                img.convert("P", palette=Image.ADAPTIVE, colors=256).save(buf, "GIF", optimize=True)
            elif pil_fmt == "TIFF":
                img.save(buf, "TIFF", compression="tiff_lzw")
            else:  # BMP
                img.save(buf, pil_fmt)
            if pil_fmt not in ("JPEG", "WEBP") or buf.tell() <= cfg.max_upload_bytes or q <= 60:
                break
            q -= 6
        dest.write_bytes(buf.getvalue())
        return dest

    @staticmethod
    def format_from_path(path: str | Path) -> str:
        """Map a source file's extension to an OUTPUT_FORMATS key (used when OUTPUT_FORMAT=original)."""
        e = Path(path).suffix.lower().lstrip(".")
        return e if e in OUTPUT_FORMATS else "jpg"

    # ----------------------------------------------------------------- quality
    def check_quality(self, path: Path) -> QualityReport:
        size = path.stat().st_size
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                w, h = im.size
                gray = np.asarray(im.convert("L"))
        except Exception:
            return QualityReport(0, 0, size, 0.0, issues=["Image file is corrupt"])
        scale = min(1.0, 1024 / max(w, h))
        if scale < 1:
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        rep = QualityReport(w, h, size, round(sharp, 1))
        if (w, h) != (cfg.output_size, cfg.output_size):
            rep.issues.append(f"Wrong output size ({w}x{h}), expected {cfg.output_size}x{cfg.output_size}")
        if size > cfg.max_upload_bytes * 1.5:
            rep.issues.append("File size too large")
        if sharp < 20:
            rep.warnings.append("Image looks soft")
        return rep

    def process_file(self, src: Path, dest_stem: Path, crop_box: dict | None, fmt: str | None = None) -> tuple[Path, QualityReport]:
        """crop -> adjust -> optimise -> verify. Raises AppError on hard failure."""
        try:
            with Image.open(src) as im:
                im.load()
                out = self.adjust_image(self.crop_image(im.copy(), crop_box))
            dest = self.optimize_image(out, dest_stem, fmt)
        except Exception as e:  # noqa: BLE001
            raise AppError("image_processing_failed", f"Could not process {src.name}: {e}", 500)
        rep = self.check_quality(dest)
        return dest, rep