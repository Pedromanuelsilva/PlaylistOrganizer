from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app.config import get_settings
from app.database import get_session, init_db
from app.models import Credential, CredentialStatus, JobState, Provider, ValidationJob, ValidationRun, utcnow
from app.services.importer import import_m3u_text
from app.services.validator import run_validation_job
from app.services.xtream_categories import fetch_categories


BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Local M3U Credential Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    providers = session.exec(select(Provider).where(Provider.is_archived == False)).all()
    credentials = session.exec(select(Credential).where(Credential.is_archived == False)).all()
    status_counts = Counter(credential.status for credential in credentials)
    latest_job = session.exec(select(ValidationJob).order_by(col(ValidationJob.created_at).desc())).first()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "provider_count": len(providers),
            "credential_count": len(credentials),
            "status_counts": status_counts,
            "latest_job": latest_job,
        },
    )


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "import.html", {"summary": None})


@app.post("/import", response_class=HTMLResponse)
async def import_links(
    request: Request,
    links: str = Form(""),
    upload: UploadFile | None = File(None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    text_parts = [links or ""]
    if upload and upload.filename:
        raw = await upload.read()
        text_parts.append(raw.decode("utf-8", errors="replace"))
    summary = import_m3u_text(session, "\n".join(text_parts))
    return templates.TemplateResponse(request, "import.html", {"summary": summary})


@app.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    providers = session.exec(
        select(Provider).where(Provider.is_archived == False).order_by(Provider.host, Provider.port)
    ).all()
    rows = [_provider_row(session, provider) for provider in providers]
    return templates.TemplateResponse(request, "providers.html", {"rows": rows})


@app.get("/providers/{provider_id}", response_class=HTMLResponse)
def provider_detail(provider_id: int, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    provider = session.get(Provider, provider_id)
    if provider is None:
        return _redirect("/providers")
    credentials = session.exec(
        select(Credential)
        .where(Credential.provider_id == provider_id, Credential.is_archived == False)
        .order_by(Credential.username)
    ).all()
    return templates.TemplateResponse(request, "provider_detail.html", {"provider": provider, "credentials": credentials})


@app.post("/credentials/{credential_id}/validate")
def validate_credential_action(
    credential_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    credential = session.get(Credential, credential_id)
    if credential is None:
        return _redirect("/providers")
    job = _create_validation_job(session, "single", [credential])
    background_tasks.add_task(run_validation_job, job.id, [credential.id])
    return _redirect(f"/validation/jobs/{job.id}")


@app.get("/credentials/{credential_id}/categories")
async def credential_categories(
    credential_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    credential = session.get(Credential, credential_id)
    if credential is None:
        return _redirect("/providers")
    provider = session.get(Provider, credential.provider_id)
    if provider is None:
        return _redirect("/providers")

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.validation_timeout_seconds, follow_redirects=True) as client:
        categories = await fetch_categories(client, provider, credential)

    credentials = session.exec(
        select(Credential)
        .where(Credential.provider_id == provider.id, Credential.is_archived == False)
        .order_by(Credential.username)
    ).all()

    return templates.TemplateResponse(
        request,
        "provider_detail.html",
        {
            "provider": provider,
            "credentials": credentials,
            "selected_credential_id": credential.id,
            "categories": categories,
        },
    )


@app.post("/providers/{provider_id}/validate")
def validate_provider_action(
    provider_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    credentials = session.exec(
        select(Credential).where(Credential.provider_id == provider_id, Credential.is_archived == False)
    ).all()
    job = _create_validation_job(session, "provider", credentials)
    background_tasks.add_task(run_validation_job, job.id, [credential.id for credential in credentials])
    return _redirect(f"/validation/jobs/{job.id}")


@app.post("/validation/validate-all")
def validate_all_action(background_tasks: BackgroundTasks, session: Session = Depends(get_session)) -> RedirectResponse:
    credentials = session.exec(
        select(Credential)
        .join(Provider, Credential.provider_id == Provider.id)
        .where(Credential.is_archived == False, Provider.is_archived == False)
    ).all()
    job = _create_validation_job(session, "all_active", credentials)
    background_tasks.add_task(run_validation_job, job.id, [credential.id for credential in credentials])
    return _redirect(f"/validation/jobs/{job.id}")


@app.get("/validation/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    jobs = session.exec(select(ValidationJob).order_by(col(ValidationJob.created_at).desc()).limit(50)).all()
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})


@app.get("/validation/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    job = session.get(ValidationJob, job_id)
    if job is None:
        return _redirect("/validation/jobs")
    runs = session.exec(
        select(ValidationRun).where(ValidationRun.job_id == job_id).order_by(col(ValidationRun.checked_at).desc())
    ).all()
    return templates.TemplateResponse(request, "job_detail.html", {"job": job, "runs": runs})


@app.post("/validation/jobs/{job_id}/archive-invalid")
def archive_invalid_from_job(job_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    invalid_runs = session.exec(
        select(ValidationRun).where(
            ValidationRun.job_id == job_id,
            ValidationRun.raw_status == CredentialStatus.INVALID.value,
        )
    ).all()
    for run in invalid_runs:
        credential = session.get(Credential, run.credential_id)
        if credential:
            credential.is_archived = True
            credential.updated_at = utcnow()
            session.add(credential)
    session.commit()
    return _redirect(f"/validation/jobs/{job_id}")


@app.post("/credentials/{credential_id}/archive")
def archive_credential(credential_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    credential = session.get(Credential, credential_id)
    if credential is None:
        return _redirect("/providers")
    provider_id = credential.provider_id
    credential.is_archived = True
    credential.updated_at = utcnow()
    session.add(credential)
    session.commit()
    return _redirect(f"/providers/{provider_id}")


@app.post("/providers/archive-no-valid")
def archive_providers_without_valid(session: Session = Depends(get_session)) -> RedirectResponse:
    # Find provider IDs that have at least one valid, non-archived credential
    valid_provider_ids = session.exec(
        select(Credential.provider_id).distinct().where(
            Credential.status == CredentialStatus.VALID,
            Credential.is_archived == False,
        )
    ).all()
    valid_set = set(valid_provider_ids)

    # Archive all active providers without any valid credential
    providers = session.exec(select(Provider).where(Provider.is_archived == False)).all()
    archived_count = 0
    for provider in providers:
        if provider.id in valid_set:
            continue
        # Archive non-valid credentials on this provider
        credentials = session.exec(
            select(Credential).where(
                Credential.provider_id == provider.id,
                Credential.is_archived == False,
                Credential.status != CredentialStatus.VALID,
            )
        ).all()
        for credential in credentials:
            credential.is_archived = True
            credential.updated_at = utcnow()
            session.add(credential)
        provider.is_archived = True
        provider.updated_at = utcnow()
        session.add(provider)
        archived_count += 1

    session.commit()
    return _redirect("/providers")


@app.post("/providers/{provider_id}/archive")
def archive_provider(provider_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    provider = session.get(Provider, provider_id)
    if provider:
        provider.is_archived = True
        provider.updated_at = utcnow()
        session.add(provider)
        session.commit()
    return _redirect("/providers")


@app.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    providers = session.exec(select(Provider).where(Provider.is_archived == True).order_by(Provider.host)).all()
    credentials = session.exec(
        select(Credential).where(Credential.is_archived == True).order_by(col(Credential.updated_at).desc())
    ).all()
    provider_map = {provider.id: provider for provider in session.exec(select(Provider)).all()}
    return templates.TemplateResponse(
        request,
        "archive.html",
        {"providers": providers, "credentials": credentials, "provider_map": provider_map},
    )


@app.post("/archive/credentials/{credential_id}/restore")
def restore_credential(credential_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    credential = session.get(Credential, credential_id)
    if credential:
        credential.is_archived = False
        credential.updated_at = utcnow()
        session.add(credential)
        session.commit()
    return _redirect("/archive")


@app.post("/archive/credentials/{credential_id}/delete")
def delete_credential(credential_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    credential = session.get(Credential, credential_id)
    if credential:
        runs = session.exec(select(ValidationRun).where(ValidationRun.credential_id == credential_id)).all()
        for run in runs:
            session.delete(run)
        session.delete(credential)
        session.commit()
    return _redirect("/archive")


@app.post("/archive/providers/{provider_id}/restore")
def restore_provider(provider_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    provider = session.get(Provider, provider_id)
    if provider:
        provider.is_archived = False
        provider.updated_at = utcnow()
        session.add(provider)
        session.commit()
    return _redirect("/archive")


@app.post("/archive/providers/{provider_id}/delete")
def delete_provider(provider_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    provider = session.get(Provider, provider_id)
    if provider:
        credentials = session.exec(select(Credential).where(Credential.provider_id == provider_id)).all()
        for credential in credentials:
            runs = session.exec(select(ValidationRun).where(ValidationRun.credential_id == credential.id)).all()
            for run in runs:
                session.delete(run)
            session.delete(credential)
        session.delete(provider)
        session.commit()
    return _redirect("/archive")


def _provider_row(session: Session, provider: Provider) -> dict:
    credentials = session.exec(
        select(Credential).where(Credential.provider_id == provider.id, Credential.is_archived == False)
    ).all()
    counts = Counter(credential.status for credential in credentials)
    method_counts = _valid_method_counts(session, credentials)
    return {
        "provider": provider,
        "credential_count": len(credentials),
        "counts": counts,
        "method_counts": method_counts,
        "validation_path": _validation_path_label(method_counts),
    }


def _valid_method_counts(session: Session, credentials: list[Credential]) -> Counter:
    method_counts: Counter = Counter()
    for credential in credentials:
        if credential.status != CredentialStatus.VALID:
            continue
        latest_valid_run = session.exec(
            select(ValidationRun)
            .where(
                ValidationRun.credential_id == credential.id,
                ValidationRun.raw_status == CredentialStatus.VALID.value,
            )
            .order_by(col(ValidationRun.checked_at).desc())
        ).first()
        if latest_valid_run is None:
            method_counts["unknown"] += 1
        else:
            method_counts[latest_valid_run.method] += 1
    return method_counts


def _validation_path_label(method_counts: Counter) -> str:
    xtream_count = method_counts.get("xtream_api", 0)
    playlist_count = method_counts.get("playlist_fetch", 0)
    unknown_count = method_counts.get("unknown", 0)
    if xtream_count and playlist_count:
        return "Mixed"
    if xtream_count:
        return "Xtream API"
    if playlist_count:
        return "Direct M3U only"
    if unknown_count:
        return "Valid, method unknown"
    return "No valid credentials"


def _create_validation_job(session: Session, job_type: str, credentials: list[Credential]) -> ValidationJob:
    provider_ids = {credential.provider_id for credential in credentials}
    job = ValidationJob(
        job_type=job_type,
        state=JobState.QUEUED,
        total=len(credentials),
        providers_affected=len(provider_ids),
        message="Queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)
