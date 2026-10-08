from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import cfg
from app.database import settings_store
from app.database.db import session_scope
from app.models.models import Image, Job
from app.schemas.api import (
    BatchRequest, ConnectRequest, CreateJobRequest, ImageOut, InspectRequest, JobOut, SearchRequest, SelectionUpdate, SettingsUpdate,
)
from app.services.container import services as svc
from app.services.description_parser_service import host_of
from app.services.job_service import BUSY
from app.utils.errors import AppError

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ helpers
def image_out(img: Image) -> ImageOut:
    o = ImageOut.model_validate(img)
    o.source_preview = f"/api/images/{img.id}/file?variant=source"
    o.processed_preview = f"/api/images/{img.id}/file?variant=processed" if img.processed_path else None
    return o


def job_out(job: Job) -> JobOut:
    visible = [image_out(i) for i in job.images if i.state != "rejected" or i.analysis]
    return JobOut(
        id=job.id, product_name=job.product_name, product_id=job.product_id, source_product_url=job.source_product_url,
        original_source_url=job.original_source_url, destination_site=job.destination_site, mode=job.mode,
        status=job.status, error_code=job.error_code, error_message=job.error_message,
        progress=job.progress or [], warnings=job.warnings or [], verification=job.verification or [],
        result_url=job.result_url, created_at=job.created_at, completed_at=job.completed_at, images=visible,
    )


def _busy() -> bool:
    """True only when work is really running in this process (a job holds the job lock, or a batch is running).
    A job still marked busy in the database while nothing is running was left behind by a restart or crash:
    it is marked failed here instead of blocking every new job forever."""
    if svc.jobs._lock.locked() or any(b["state"] == "running" for b in svc.jobs.batches.values()):
        return True
    with session_scope() as s:
        for j in s.query(Job).filter(Job.status.in_(list(BUSY))).all():
            j.status = "failed"
            j.error_code = "interrupted"
            j.error_message = "Interrupted by a restart or crash."
    return False


# ---------------------------------------------------------------- wordpress
@router.post("/wordpress/connect")
async def connect(body: ConnectRequest):
    headless = body.headless if body.headless is not None else settings_store.get("headless")
    await svc.wp.connect(body.login_url, body.username, body.password.get_secret_value(), bool(headless))
    settings_store.set_many({"headless": bool(headless)})
    return {"connected": True, "site": svc.wp.base_url, "username": svc.wp.username, "headless": svc.pw.headless}


@router.get("/wordpress/status")
async def wp_status():
    return {"connected": svc.wp.connected, "site": svc.wp.base_url, "username": svc.wp.username,
            "claude_configured": bool(cfg.anthropic_api_key)}


@router.post("/wordpress/logout")
async def logout():
    await svc.wp.logout()
    return {"connected": False}


# ----------------------------------------------------------------- products
def _require_wp() -> None:
    if not svc.wp.connected:
        raise AppError("wordpress_not_connected", "Connect to WordPress first.", 409)


@router.post("/products/search")
async def search_products(body: SearchRequest):
    _require_wp()
    matches = await svc.search.search(body.query.strip())
    if not matches:
        raise AppError("product_not_found", f"No product matching '{body.query}' was found on the connected site.", 404)
    threshold = float(settings_store.get("match_threshold"))
    auto = svc.search.auto_select(matches, threshold)
    return {"matches": matches, "auto_selected_id": auto["id"] if auto else None,
            "auto_selected_url": auto["permalink"] if auto else None, "threshold": threshold}


@router.post("/products/inspect")
async def inspect_product(body: InspectRequest):
    _require_wp()
    if not body.product_id and not body.product_url:
        raise AppError("product_not_found", "Select a product first.", 422)
    prod = await svc.wp.get_product_description(body.product_id, body.product_url)
    det = svc.source_urls.detect(prod.combined_html, prod.name, svc.wp.base_url)
    from bs4 import BeautifulSoup

    preview = BeautifulSoup(prod.combined_html, "html.parser").get_text(" ", strip=True)[:600]
    return {
        "product": {"id": prod.id, "name": prod.name, "url": prod.permalink, "status": prod.status},
        "description_preview": preview,
        "candidates": [svc.source_urls.candidate_dict(c) for c in det.candidates[:8]],
        "selected_url": det.selected.url if det.selected else None,
        "ambiguous": det.ambiguous,
        "destination_host": host_of(svc.wp.base_url or ""),
        "message": None if det.selected else "Source URL not found.",
    }


# --------------------------------------------------------------------- jobs
@router.post("/jobs", response_model=JobOut)
async def create_job(body: CreateJobRequest):
    _require_wp()
    if _busy():
        raise AppError("job_in_progress", "Another job is still running. Wait for it to finish.", 409)
    if body.mode == "update" and not (body.product_id or body.product_url):
        raise AppError("product_not_found", "Select an existing product to update.", 422)
    if body.mode == "create" and not body.original_source_url:
        raise AppError("source_url_not_found", "A new listing needs a source URL.", 422)
    jid = svc.jobs.create_job(body.product_name, body.product_id, body.product_url, body.mode, body.original_source_url)
    svc.jobs.launch(svc.jobs.run(jid))
    return job_out(svc.jobs.get(jid))


def _recent_jobs() -> list[Job]:
    with session_scope() as s:
        return list(s.query(Job).order_by(Job.created_at.desc()).limit(10).all())


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs():
    return [job_out(svc.jobs.get(j.id)) for j in _recent_jobs()]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str):
    return job_out(svc.jobs.get(job_id))


@router.put("/jobs/{job_id}/selection", response_model=JobOut)
async def update_selection(job_id: str, body: SelectionUpdate):
    job = svc.jobs.get(job_id)
    if job.status != "ready_for_review":
        raise AppError("job_not_ready", "Selection can only be edited while the job awaits review.", 409)
    valid_ids = {i.id for i in job.images if i.state == "valid"}
    ids = list(dict.fromkeys(body.image_ids))
    if not set(ids) <= valid_ids or body.best_image_id not in ids:
        raise AppError("invalid_selection", "Selection contains unknown images or the best image is not selected.", 422)
    svc.jobs._set(job_id, status="processing", error_code=None, error_message=None)
    svc.jobs.launch(svc.jobs.apply_selection(job_id, ids, body.best_image_id))
    return job_out(svc.jobs.get(job_id))


@router.post("/jobs/{job_id}/publish", response_model=JobOut)
async def publish_job(job_id: str):
    job = svc.jobs.get(job_id)
    if job.status not in ("ready_for_review", "verification_failed"):
        raise AppError("job_not_ready", "The job is not ready to publish.", 409)
    svc.jobs._set(job_id, status="publishing", error_code=None, error_message=None)
    svc.jobs.launch(svc.jobs.publish(job_id))
    return job_out(svc.jobs.get(job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str):
    await svc.jobs.cancel(job_id)
    return job_out(svc.jobs.get(job_id))


# ------------------------------------------------------------------ batches
def _batch_out(batch_id: str) -> dict:
    """Batch state plus, for the product being processed right now, which step its job is on."""
    b = svc.jobs.get_batch(batch_id)
    items = []
    for it in b["items"]:
        it = dict(it)
        it["step"], it["steps_done"], it["steps_total"] = None, 0, 0
        if it["status"] == "running" and it.get("job_id"):
            try:
                prog = svc.jobs.get(it["job_id"]).progress or []
                run = next((p for p in prog if p.get("status") == "running"), None)
                it["step"] = run["label"] if run else None
                it["steps_done"] = sum(1 for p in prog if p.get("status") in ("done", "skipped"))
                it["steps_total"] = len(prog)
            except AppError:
                pass
        items.append(it)
    return {**b, "items": items}


@router.post("/batches")
async def start_batch(body: BatchRequest):
    """'Use all One by One': every draft, non-copy product is processed and published, one after the other."""
    _require_wp()
    if any(b["state"] == "running" for b in svc.jobs.batches.values()):
        raise AppError("batch_in_progress", "A batch is already running. Stop it or wait for it to finish.", 409)
    if _busy():
        raise AppError("job_in_progress", "Another job is still running. Wait for it to finish.", 409)
    batch_id = svc.jobs.start_batch([p.model_dump() for p in body.products])
    return _batch_out(batch_id)


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str):
    return _batch_out(batch_id)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    svc.jobs.cancel_batch(batch_id)  # stops after the product currently being processed
    return _batch_out(batch_id)


@router.get("/images/{image_id}/file")
async def image_file(image_id: int, variant: str = "source"):
    with session_scope() as s:
        img = s.get(Image, image_id)
    if img is None:
        raise AppError("image_not_found", "Image not found.", 404)
    raw = img.processed_path if variant == "processed" else img.local_path
    if not raw:
        raise AppError("image_not_found", "Image file not available.", 404)
    path = Path(raw).resolve()
    if not path.is_relative_to(cfg.jobs_dir.resolve()) or not path.is_file():
        raise AppError("image_not_found", "Image file not available.", 404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})


# ----------------------------------------------------------------- settings
@router.get("/settings")
async def get_settings():
    return settings_store.get_all()


@router.put("/settings")
async def put_settings(body: SettingsUpdate):
    return settings_store.set_many(body.model_dump(exclude_none=True))