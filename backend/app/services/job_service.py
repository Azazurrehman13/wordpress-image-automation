"""Orchestrates the exact workflow: description -> source URL -> source images -> AI -> process -> upload -> review -> publish."""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from app.config import cfg
from app.database import settings_store
from app.database.db import session_scope
from app.models.models import Image, Job
from app.schemas.ai import ImageAnalysis
from app.services.claude_service import ImageInput
from app.services.image_selection_service import SelectionCandidate
from app.services.wordpress_service import is_copy_product
from app.utils.errors import AppError
from app.utils.logging_utils import job_log
from app.utils.security import slugify

log = logging.getLogger("jobs")

STEPS = [
    ("wp_connected", "WordPress connected"),
    ("product_found", "Product found"),
    ("description_opened", "Product description opened"),
    ("source_url_found", "Original source URL found"),
    ("source_opened", "Original source opened"),
    ("images_extracted", "Product images extracted"),
    ("images_downloaded", "Images downloaded"),
    ("duplicates_removed", "Duplicate images removed"),
    ("claude_analyzed", "Claude analyzed images"),
    ("features_selected", "Best feature images selected (1–5)"),
    ("best_selected", "Best feature image selected"),
    ("images_cropped", "Images cropped"),
    ("images_optimized", "Images optimized"),
    ("metadata_generated", "Image metadata generated"),
    ("images_uploaded", "Images uploaded to WordPress"),
    ("ready", "Ready for review"),
    ("main_assigned", "Product Image assigned"),
    ("gallery_assigned", "Product Gallery assigned"),
    ("published", "Published"),
    ("verified", "Verified"),
]
EDIT_STEPS = ["images_cropped", "images_optimized", "metadata_generated", "images_uploaded", "ready"]
BUSY = {"queued", "running", "processing", "publishing"}
HEURISTIC_TRUST = 50.0  # rule-based link score trusted on its own when Claude is unsure or unavailable
MIN_IMAGES = 1  # a source with a single product image is fine: that image becomes the main image
MAX_IMAGES = 5


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class Progress:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def _set(self, key: str, status: str, detail: str = "") -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            prog = [dict(p) for p in (job.progress or [])]
            for p in prog:
                if p["key"] == key:
                    p.update(status=status, detail=detail, at=_now())
            job.progress = prog
        job_log(self.job_id, f"[{status}] {key} {detail}")

    def start(self, key: str, detail: str = "") -> None:
        self._set(key, "running", detail)

    def done(self, key: str, detail: str = "") -> None:
        self._set(key, "done", detail)

    def skip(self, key: str, detail: str = "") -> None:
        self._set(key, "skipped", detail)

    def reset(self, keys: list[str]) -> None:
        for k in keys:
            self._set(k, "pending", "")

    def fail_running(self, message: str) -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            prog = [dict(p) for p in (job.progress or [])]
            for p in prog:
                if p["status"] == "running":
                    p.update(status="error", detail=message)
            job.progress = prog


class JobService:
    def __init__(self, services) -> None:
        self.svc = services
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.batches: dict[str, dict] = {}  # Run-all batches, kept in memory (lost on restart; the jobs themselves stay in the database)

    # --------------------------------------------------------------- helpers
    def launch(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def create_job(self, name: str, product_id: int | None, product_url: str | None, mode: str, source_url: str | None) -> str:
        with session_scope() as s:
            job = Job(
                product_name=name.strip(), product_id=product_id, source_product_url=product_url, mode=mode,
                original_source_url=(source_url or "").strip() or None, status="queued",
                destination_site=self.svc.wp.base_url,
                progress=[{"key": k, "label": l, "status": "pending", "detail": ""} for k, l in STEPS],
                warnings=[], verification=[],
            )
            s.add(job)
            s.flush()
            return job.id

    def get(self, job_id: str) -> Job:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise AppError("job_not_found", "Job not found.", 404)
            _ = job.images  # load while session is open
            return job

    def _set(self, job_id: str, **fields) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            for k, v in fields.items():
                setattr(job, k, v)

    def _warn(self, job_id: str, *messages: str) -> None:
        if not messages:
            return
        with session_scope() as s:
            job = s.get(Job, job_id)
            job.warnings = [*(job.warnings or []), *messages]
        for m in messages:
            job_log(job_id, f"WARNING {m}")

    def _images(self, job_id: str) -> list[Image]:
        with session_scope() as s:
            return list(s.query(Image).filter_by(job_id=job_id).order_by(Image.id).all())

    def _img_set(self, image_id: int, **fields) -> None:
        with session_scope() as s:
            img = s.get(Image, image_id)
            for k, v in fields.items():
                setattr(img, k, v)

    @staticmethod
    def _reject_summary(results) -> str:
        """'3 × Too small; 1 × Looks like a site graphic' - so an 'only 2 images' error says what happened to the rest."""
        groups: dict[str, int] = {}
        for r in results:
            if not r.ok:
                key = re.sub(r"\s*\(.*?\)", "", r.reason or "").strip() or "Download failed"
                groups[key] = groups.get(key, 0) + 1
        return "; ".join(f"{n} × {k}" for k, n in sorted(groups.items(), key=lambda kv: -kv[1])[:4])

    # ---------------------------------------------------------- source link
    async def _resolve_source_url(self, job_id: str, description_html: str, product_name: str) -> str:
        """Find the original source link inside the description.
        Rules rank every link first; then Claude reads the description text around each link and decides."""
        from app.services.description_parser_service import clean_url

        s = self.svc
        det = s.source_urls.detect(description_html, product_name, s.wp.base_url)
        shortlist = det.candidates[:8]
        if not shortlist:
            raise AppError("source_url_not_found", "Source URL not found in the product description. Paste the source URL manually to continue.", 422)
        extracted: dict[str, object] = {}
        for eu in s.source_urls.parser.extract_urls(description_html):
            extracted[eu.url] = eu
            extracted.setdefault(clean_url(eu.url), eu)
        links = []
        for i, c in enumerate(shortlist):
            eu = extracted.get(c.url)
            links.append({"index": i, "url": c.url, "kind": c.kind, "rule_score": c.score,
                          "link_text": (getattr(eu, "anchor_text", "") or "")[:120],
                          "text_before": (getattr(eu, "context", "") or "")[-120:]})
        idx = None
        try:
            idx, reason = await s.claude.choose_source_url(description_html, product_name, links)
            job_log(job_id, f"Claude source-link review: index={idx} reason={reason}")
        except AppError as e:
            self._warn(job_id, f"Claude could not review the source links ({e.message}); the best rule-based match was used.")
        if idx is not None and 0 <= idx < len(shortlist):
            return shortlist[idx].url
        if det.selected and det.selected.score >= HEURISTIC_TRUST:
            job_log(job_id, f"Using the best rule-based source link: {det.selected.url} (score {det.selected.score})")
            return det.selected.url
        raise AppError("source_url_not_found", "Source URL not found in the product description: none of its links is clearly the original product source. "
                       "Paste the source URL manually to continue.", 422)

    # -------------------------------------------------------------- pipeline
    async def run(self, job_id: str, force_publish: bool = False) -> None:
        async with self._lock:
            try:
                await self._pipeline(job_id, force_publish)
            except AppError as e:
                self._fail(job_id, e.code, e.message)
            except Exception as e:  # noqa: BLE001
                log.exception("Job %s crashed", job_id)
                self._fail(job_id, "internal_error", f"Unexpected error: {type(e).__name__}: {e}")

    def _fail(self, job_id: str, code: str, message: str, status: str = "failed") -> None:
        Progress(job_id).fail_running(message)
        self._set(job_id, status=status, error_code=code, error_message=message)
        job_log(job_id, f"FAILED {code}: {message}")

    async def _pipeline(self, job_id: str, force_publish: bool = False) -> None:
        s = self.svc
        p = Progress(job_id)
        job = self.get(job_id)
        self._set(job_id, status="running", error_code=None, error_message=None)

        p.start("wp_connected")
        if not s.wp.connected:
            raise AppError("wordpress_not_connected", "Connect to WordPress first.", 409)
        p.done("wp_connected", s.wp.base_url or "")

        description_html = ""
        if job.mode == "update":
            p.start("product_found")
            prod = await s.wp.get_product_description(product_id=job.product_id, product_url=job.source_product_url)
            if is_copy_product(prod.name):  # duplicated products are never processed or published
                raise AppError("copy_product_blocked", f"'{prod.name}' is a copy and is never processed or published.", 409)
            # the found product's own title is what the image text is built from (not what was typed in the search box)
            self._set(job_id, product_id=prod.id, source_product_url=prod.permalink, original_status=prod.status,
                      **({"product_name": prod.name.strip()} if (prod.name or "").strip() else {}))
            job = self.get(job_id)
            p.done("product_found", prod.name)
            p.start("description_opened")
            description_html = prod.combined_html
            if not description_html.strip() and not job.original_source_url:
                raise AppError("product_description_unavailable", "The product has no readable description. Paste the source URL manually.", 422)
            p.done("description_opened", f"{len(description_html)} characters read")
        else:
            p.skip("product_found", "New listing")
            p.skip("description_opened", "New listing")
            self._set(job_id, original_status=None)

        p.start("source_url_found")
        source_url = job.original_source_url
        if not source_url:
            source_url = await self._resolve_source_url(job_id, description_html, job.product_name)
        source_url = await s.source_urls.validate(source_url)
        self._set(job_id, original_source_url=source_url)
        p.done("source_url_found", source_url)

        p.start("source_opened")
        extraction = await s.extractor.extract(source_url)
        if extraction.browser_error:
            self._warn(job_id, f"The source page could not be opened in the browser ({extraction.browser_error}); "
                               "only the plain page was read, so some gallery images may be missing.")
        p.done("source_opened", (getattr(extraction, "method", "") or ("browser" if extraction.used_browser else "plain page")) + f"; {len(extraction.candidates)} candidate images")
        p.start("images_extracted")
        if not extraction.candidates:
            raise AppError("no_product_images", "No product images were found on the original source page.", 422)
        p.done("images_extracted", f"{len(extraction.candidates)} candidate images")

        p.start("images_downloaded")
        results = await s.downloader.download_all(job_id, extraction.candidates, extraction.final_url)
        valid = [r for r in results if r.ok]
        for r in results:
            if not r.ok:
                job_log(job_id, f"rejected {r.url}: {r.reason}")
        if not valid:
            small = sum(1 for r in results if "Too small" in r.reason)
            if small and small >= len(results) / 2:
                raise AppError("images_too_low_resolution", f"All images were below {cfg.min_image_side}px. The source does not offer high-resolution images.", 422)
            raise AppError("no_product_images", f"No usable product images could be downloaded from the source. Rejected: {self._reject_summary(results)}.", 422)
        with session_scope() as db:
            for r in valid:
                db.add(Image(job_id=job_id, source_url=r.url, local_path=r.path, width=r.width, height=r.height, state="valid"))
        rej = self._reject_summary(results)
        p.done("images_downloaded", f"{len(valid)} of {len(results)} usable" + (f" (rejected: {rej})" if rej else ""))

        # IMPORTANT: Do not remove images using perceptual-hash similarity here.
        # Different product angles can legitimately have very similar hashes.
        # Every downloaded candidate must reach AI analysis so Claude can judge
        # the actual visual content and select the best 4-5 images.
        p.skip(
            "duplicates_removed",
            "Near-duplicate filtering disabled; all downloaded candidates are sent to visual analysis.",
        )

        p.start("claude_analyzed")
        pool = [r for r in self._images(job_id) if r.state == "valid"]
        pool.sort(key=lambda r: (r.width or 0) * (r.height or 0), reverse=True)
        # trusted = the gallery was read from the shop's own product data, so every image is this product's
        trusted = bool(getattr(extraction, "exact", False))
        analyses = await s.claude.analyze_images(
            [ImageInput(r.id, Path(r.local_path)) for r in pool],
            product_name=job.product_name,
            description_html=description_html,
            check_same_product=not trusted,
        )
        cands: list[SelectionCandidate] = []
        for r, a in zip(pool, analyses):
            reject = None if a.is_product_image and a.is_same_product else (
                "Not a product image" if not a.is_product_image else "Shows a different product")
            self._img_set(r.id, analysis=a.model_dump(), ai_score=a.composite,
                          **({} if reject is None else {"state": "rejected", "rejected_reason": reject}))
            cands.append(SelectionCandidate(r.id, Path(r.local_path), a))
        p.done("claude_analyzed", f"{len(analyses)} images scored")

        p.start("features_selected")

        # Select from ALL visually valid candidates. Target 5 images, with
        # a hard minimum of 4. Selection is based on angle/view/framing/quality
        # rather than perceptual-hash similarity.
        analysis_map = {r.id: a for r, a in zip(pool, analyses) if a.is_product_image and (trusted or a.is_same_product)}
        by_id = {r.id: r for r in pool}
        ranked = sorted((r for r in pool if r.id in analysis_map), key=lambda r: analysis_map[r.id].composite, reverse=True)
        if len(ranked) < MIN_IMAGES:
            why: dict[str, int] = {}
            for r, a in zip(pool, analyses):
                k = "not a product photo" if not a.is_product_image else "shows a different product"
                why[k] = why.get(k, 0) + 1
            raise AppError(
                "no_matching_product_images",
                f"None of the {len(pool)} downloaded images is a photo of this product ({'; '.join(f'{n} × {k}' for k, n in why.items())}). "
                "The product was not published.",
                422,
            )

        if len(ranked) <= MAX_IMAGES:
            # 1-5 images: every matching source image is used; a single image simply becomes the main image
            selected_ids = [r.id for r in ranked]
            notes = f"all {len(selected_ids)} matching source image(s) used"
        else:
            feature = await s.claude.select_feature_images(
                [ImageInput(r.id, Path(r.local_path)) for r in ranked], analysis_map, job.product_name, count=MAX_IMAGES)
            selected_ids = []
            for image_id in feature.selected_image_ids:
                if image_id in analysis_map and image_id not in selected_ids:
                    selected_ids.append(image_id)
            for r in ranked:  # never trust the count Claude returns: fill from the strongest remaining candidates
                if len(selected_ids) >= MAX_IMAGES:
                    break
                if r.id not in selected_ids:
                    selected_ids.append(r.id)
            selected_ids = selected_ids[:MAX_IMAGES]
            notes = feature.coverage_notes

        if not trusted:
            # Final safety net for scraped pages: every chosen image must show the product described. Odd ones are
            # dropped (and replaced by the next best candidate when there is one). Nothing left = not published.
            rejected_odd: set[int] = set()
            for _ in range(3):
                if not selected_ids:
                    break
                odd = await s.claude.find_odd_images(
                    [ImageInput(i, Path(by_id[i].local_path)) for i in selected_ids], job.product_name, description_html)
                if not odd:
                    break
                rejected_odd.update(odd)
                selected_ids = [i for i in selected_ids if i not in rejected_odd]
                spare = [r for r in ranked if r.id not in selected_ids and r.id not in rejected_odd]
                selected_ids += [r.id for r in spare[: MAX_IMAGES - len(selected_ids)]]
            if len(selected_ids) < MIN_IMAGES:
                raise AppError(
                    "mixed_product_images",
                    "No image on the source page matches this exact product (the page shows other products). The product was not published.",
                    422,
                )

        if len(selected_ids) == 1:
            best_id = selected_ids[0]
        else:
            best = await s.claude.select_best_feature_image(
                [ImageInput(i, Path(by_id[i].local_path)) for i in selected_ids], job.product_name)
            best_id = best.best_image_id if best.best_image_id in selected_ids else selected_ids[0]

        p.done("features_selected", f"{len(selected_ids)} image(s) selected (1-5); {notes}")
        p.start("best_selected")
        p.done("best_selected", f"image {best_id}")

        await self._finalize(job_id, selected_ids, best_id, p)
        self._set(job_id, status="ready_for_review")
        p.done("ready", "Review the result, then publish")

        # Run all publishes every product that got through the pipeline, warnings or not. Normal auto-publish still waits for zero warnings.
        if force_publish or (settings_store.get("auto_publish") and not self.get(job_id).warnings):
            await self._publish(job_id)

    # -------------------------------------------------------------- finalize
    async def _finalize(self, job_id: str, ordered_ids: list[int], best_id: int, p: Progress) -> None:
        """crop/adjust -> optimise -> verify -> metadata -> upload. Reused by selection edits."""
        s = self.svc
        job = self.get(job_id)
        order = [best_id] + [i for i in ordered_ids if i != best_id]
        imgs = {i.id: i for i in self._images(job_id)}
        recs = [imgs[i] for i in order]
        if len(recs) < MIN_IMAGES:
            raise AppError("insufficient_selected_images", "At least 1 image is required for the final product listing.", 422)
        if len(recs) > MAX_IMAGES:
            recs = recs[:MAX_IMAGES]
        out_dir = cfg.jobs_dir / job_id / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = s.processor
        warnings: list[str] = []

        p.start("images_cropped")
        from PIL import Image as PILImage  # local import keeps module import light

        def crop_adjust(rec: Image) -> None:
            box = (rec.analysis or {}).get("crop_box")

            # Keep a visible safety margin around the product. The AI crop is
            # intentionally expanded before processing so the product never
            # appears visually pressed against the frame edge.
            if box:
                margin = 0.06
                x = max(0.0, float(box.get("x", 0.0)) - margin)
                y = max(0.0, float(box.get("y", 0.0)) - margin)
                right = min(1.0, float(box.get("x", 0.0)) + float(box.get("w", 1.0)) + margin)
                bottom = min(1.0, float(box.get("y", 0.0)) + float(box.get("h", 1.0)) + margin)
                box = {"x": x, "y": y, "w": right - x, "h": bottom - y}

            with PILImage.open(rec.local_path) as im:
                im.load()
                out = proc.adjust_image(proc.crop_image(im.copy(), box))
            out.save(out_dir / f"{rec.id}_stage.png")

        todo = [r for r in recs if not r.processed_path]
        for r in todo:
            await asyncio.to_thread(crop_adjust, r)
        p.done("images_cropped", f"{len(todo)} images processed")

        p.start("images_optimized")
        kept: list[Image] = []
        for r in recs:
            if r.processed_path:
                kept.append(r)
                continue
            stage = out_dir / f"{r.id}_stage.png"

            def optimise(rec=r, stage=stage):
                with PILImage.open(stage) as im:
                    im.load()
                    fmt = proc.format_from_path(rec.local_path) if cfg.output_format == "original" else None
                    dest = proc.optimize_image(im.copy(), out_dir / f"{rec.id}", fmt)
                return dest, proc.check_quality(dest)

            dest, rep = await asyncio.to_thread(optimise)
            stage.unlink(missing_ok=True)
            if not rep.ok:
                if r.id == best_id:
                    raise AppError("image_processing_failed", f"The best image failed quality checks: {'; '.join(rep.issues)}", 422)
                warnings.append(f"Image {r.id} dropped: {'; '.join(rep.issues)}")
                continue
            for w in rep.warnings:
                warnings.append(f"Image {r.id}: {w}")
            self._img_set(r.id, processed_path=str(dest))
            r.processed_path = str(dest)
            kept.append(r)
        recs = kept
        p.done("images_optimized", f"{len(recs)} images ready")

        p.start("metadata_generated")
        from app.services.description_parser_service import host_of
        from app.services.image_metadata import build_metadata

        # ALT TEXT RULE: the alt text of every image is the original product title, character for character.
        # No view labels, no variations, no extra words (HTML entities such as &amp; are turned back into the real character).
        original_title = _html.unescape(job.product_name or "").strip()
        used_labels: dict[str, int] = {}
        banned = [host_of(job.original_source_url or "")]
        types = {r.id: (r.analysis or {}).get("image_type", "other") for r in recs}
        mds = [build_metadata(job.product_name, types[r.id], pos == 0, used_labels, banned) for pos, r in enumerate(recs)]  # recs[0] = main
        for r, md in zip(recs, mds):
            md["alt_text"] = original_title
            if (r.title, r.alt_text, r.caption, r.description) != (md["title"], md["alt_text"], md["caption"], md["description"]):
                self._img_set(r.id, **md)
                r.title, r.alt_text, r.caption, r.description = md["title"], md["alt_text"], md["caption"], md["description"]
                if r.wordpress_media_id:  # already in the media library (e.g. its role changed): keep WordPress in sync
                    await s.wp.update_media_metadata(r.wordpress_media_id, md)
        p.done("metadata_generated", f"{len(recs)} images described")

        p.start("images_uploaded")
        slug = slugify(job.product_name)
        for n, r in enumerate(recs, 1):
            if r.wordpress_media_id:
                continue
            ext = Path(r.processed_path).suffix
            res = await s.wp.upload_media(Path(r.processed_path), {
                "filename": f"{slug}-{n}{ext}", "title": r.title, "alt_text": r.alt_text,
                "caption": r.caption, "description": r.description})
            self._img_set(r.id, wordpress_media_id=res["id"])
            r.wordpress_media_id = res["id"]
        p.done("images_uploaded", f"{len(recs)} images in the media library")

        final_ids = [r.id for r in recs]
        for old in self._images(job_id):
            if old.id not in final_ids:
                if old.wordpress_media_id:
                    await s.wp.delete_media(old.wordpress_media_id)
                self._img_set(old.id, selected=False, is_featured=False, position=None,
                              **({"wordpress_media_id": None, "processed_path": None, "title": None} if old.wordpress_media_id else {}))
        for pos, r in enumerate(recs):
            self._img_set(r.id, selected=True, is_featured=(pos == 0), position=pos)
        self._warn(job_id, *warnings)

    # ----------------------------------------------------------- user edits
    async def apply_selection(self, job_id: str, image_ids: list[int], best_id: int) -> None:
        if not MIN_IMAGES <= len(set(image_ids)) <= MAX_IMAGES:
            raise AppError(
                "invalid_selection_count",
                "A product must have between 1 and 5 selected images.",
                422,
            )
        if best_id not in image_ids:
            raise AppError(
                "invalid_best_image",
                "The main image must be one of the selected images.",
                422,
            )
        async with self._lock:
            p = Progress(job_id)
            try:
                p.reset(EDIT_STEPS)
                await self._finalize(job_id, image_ids, best_id, p)
                self._set(job_id, status="ready_for_review", error_code=None, error_message=None)
                p.done("ready", "Selection updated")
            except AppError as e:
                p.fail_running(e.message)
                self._set(job_id, status="ready_for_review", error_code=e.code, error_message=e.message)
            except Exception as e:  # noqa: BLE001
                log.exception("Selection edit failed")
                p.fail_running(str(e))
                self._set(job_id, status="ready_for_review", error_code="internal_error", error_message=f"{type(e).__name__}: {e}")

    async def cancel(self, job_id: str) -> None:
        async with self._lock:
            job = self.get(job_id)
            if job.status in ("completed",):
                raise AppError("job_completed", "A published job cannot be cancelled.", 409)
            for img in self._images(job_id):
                if img.wordpress_media_id:
                    try:
                        await self.svc.wp.delete_media(img.wordpress_media_id)
                    except Exception:  # noqa: BLE001
                        pass
                    self._img_set(img.id, wordpress_media_id=None)
            self._set(job_id, status="cancelled", completed_at=datetime.utcnow())

    # ------------------------------------------------------------ run all
    def start_batch(self, products: list[dict]) -> str:
        """Process every found product one by one and publish each automatically.
        `products` are search results: {"id", "name", "permalink", "status"}.
        Copies ("... (Copy)"), non-draft items and items without an ID are skipped and never published."""
        if not self.svc.wp.connected:
            raise AppError("wordpress_not_connected", "Connect to WordPress first.", 409)
        items = []
        for prod in products:
            name = (prod.get("name") or "").strip()
            item = {"product_id": prod.get("id"), "name": name, "permalink": prod.get("permalink") or "",
                    "status": "queued", "message": "", "job_id": None, "result_url": None}
            if is_copy_product(name):
                item.update(status="skipped", message="Copy product: never processed or published")
            elif prod.get("id") is None:
                item.update(status="skipped", message="No product ID (found by page search only)")
            elif (prod.get("status") or "").lower() not in cfg.search_statuses:
                item.update(status="skipped", message=f"Status is '{prod.get('status')}', not one of {', '.join(cfg.search_statuses)}")
            items.append(item)
        batch_id = uuid.uuid4().hex[:12]
        self.batches[batch_id] = {"id": batch_id, "state": "running", "cancel": False, "items": items}
        self.launch(self._run_batch(batch_id))
        return batch_id

    def get_batch(self, batch_id: str) -> dict:
        b = self.batches.get(batch_id)
        if b is None:
            raise AppError("batch_not_found", "Batch not found.", 404)
        counts: dict[str, int] = {}
        for it in b["items"]:
            counts[it["status"]] = counts.get(it["status"], 0) + 1
        return {"id": b["id"], "state": b["state"], "counts": counts, "total": len(b["items"]), "items": b["items"]}

    def cancel_batch(self, batch_id: str) -> None:
        """Stops after the product currently being processed; the rest are not started."""
        b = self.batches.get(batch_id)
        if b is None:
            raise AppError("batch_not_found", "Batch not found.", 404)
        b["cancel"] = True

    async def _run_batch(self, batch_id: str) -> None:
        b = self.batches[batch_id]
        try:
            for item in b["items"]:
                if item["status"] != "queued":
                    continue
                if b["cancel"]:
                    item.update(status="cancelled", message="Batch stopped before this product")
                    continue
                item["status"] = "running"
                try:
                    job_id = self.create_job(item["name"], item["product_id"], item["permalink"], "update", None)
                    item["job_id"] = job_id
                    await self.run(job_id, force_publish=True)  # one at a time (run() holds the job lock)
                    job = self.get(job_id)
                    if job.status == "completed":
                        notes = "; ".join(job.warnings or [])
                        item.update(status="published", message="Published and verified" + (f" (notes: {notes})" if notes else ""), result_url=job.result_url)
                    elif job.status == "ready_for_review":
                        item.update(status="needs_review", message="Left as draft: " + ("; ".join(job.warnings or []) or job.error_message or "needs a manual check"))
                    else:
                        item.update(status="failed", message=job.error_message or job.status, result_url=job.result_url)
                except Exception as e:  # noqa: BLE001  one bad product must not stop the rest
                    log.exception("Batch item %s crashed", item["name"])
                    item.update(status="failed", message=f"{type(e).__name__}: {e}")
        finally:
            b["state"] = "cancelled" if b["cancel"] else "done"

    # --------------------------------------------------------------- publish
    async def publish(self, job_id: str) -> None:
        async with self._lock:
            try:
                await self._publish(job_id)
            except AppError as e:
                self._fail(job_id, e.code, e.message, status="ready_for_review" if e.code != "verification_failed" else "verification_failed")
            except Exception as e:  # noqa: BLE001
                log.exception("Publish failed")
                self._fail(job_id, "internal_error", f"{type(e).__name__}: {e}", status="ready_for_review")

    async def _publish(self, job_id: str) -> None:
        s = self.svc
        p = Progress(job_id)
        job = self.get(job_id)
        if job.status not in ("ready_for_review", "verification_failed", "publishing"):
            raise AppError("job_not_ready", "The job is not ready to publish.", 409)
        self._set(job_id, status="publishing", error_code=None, error_message=None)
        sel = sorted([i for i in self._images(job_id) if i.selected and i.wordpress_media_id], key=lambda i: i.position or 0)
        if not sel:
            raise AppError("no_images_selected", "No images are selected.", 422)
        media_ids = [i.wordpress_media_id for i in sel]
        if not MIN_IMAGES <= len(media_ids) <= MAX_IMAGES:
            raise AppError(
                "invalid_publish_image_count",
                f"Publishing requires 1 to 5 product images; {len(media_ids)} are currently selected.",
                422,
            )

        desc = short = None
        if job.mode == "update" and job.product_id:
            current = await s.wp.get_product(job.product_id, raw=True)  # stored content, so only the link changes
            desc, short = s.listing.clean_content(current, job.original_source_url, bool(settings_store.get("strip_source_from_description")))

        p.reset(["main_assigned", "gallery_assigned", "published", "verified"])
        p.start("main_assigned")
        product = await s.wp.create_or_update_product(
            job.product_id, name=job.product_name, media_ids=media_ids, description=desc, short_description=short,
            status=None if job.mode == "update" else "draft")
        pid = product["id"]
        self._set(job_id, product_id=pid)
        p.done("main_assigned", f"media {media_ids[0]}")
        p.start("gallery_assigned")
        p.done("gallery_assigned", f"{len(media_ids) - 1} gallery images")

        p.start("published")
        if product.get("status") != "publish":  # draft, pending or private -> make it public
            product = await s.wp.publish_product(pid)
            if product.get("status") != "publish":  # e.g. the WordPress user may not be allowed to publish
                raise AppError("publish_failed", f"WordPress kept the product as '{product.get('status')}' instead of public. "
                               "Check that the connected user may publish products.", 502)
            p.done("published", "Published publicly")
        else:
            p.done("published", "Already public")
        link = product.get("permalink", "")

        p.start("verified")
        checks = await s.wp.verify_product(pid, media_ids, s.listing.forbidden_strings(job.original_source_url))
        self._set(job_id, verification=checks, result_url=link)
        failed = [c for c in checks if not c["ok"]]
        if failed:
            raise AppError("verification_failed", "Verification failed: " + "; ".join(c["name"] for c in failed), 502)
        p.done("verified", f"{len(checks)} checks passed")
        self._set(job_id, status="completed", completed_at=datetime.utcnow())