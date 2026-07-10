import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.models import (
    Credential,
    CredentialStatus,
    JobState,
    Provider,
    ValidationJob,
    ValidationRun,
    utcnow,
)


@dataclass(frozen=True)
class ValidationOutcome:
    status: CredentialStatus
    method: str
    success: bool
    http_status: int | None
    message: str
    raw_status: str = ""
    account_metadata: dict[str, Any] | None = None
    expires_at: datetime | None = None


async def run_validation_job(job_id: int, credential_ids: list[int]) -> None:
    settings = get_settings()
    semaphore = asyncio.Semaphore(max(1, settings.validation_concurrency))

    with Session(engine) as session:
        job = session.get(ValidationJob, job_id)
        if job is None:
            return
        job.state = JobState.RUNNING
        job.started_at = utcnow()
        job.total = len(credential_ids)
        session.add(job)
        session.commit()

    try:
        async with httpx.AsyncClient(timeout=settings.validation_timeout_seconds, follow_redirects=True) as client:
            tasks = [_validate_one(job_id, credential_id, client, semaphore) for credential_id in credential_ids]
            await asyncio.gather(*tasks)
    except Exception as exc:
        with Session(engine) as session:
            job = session.get(ValidationJob, job_id)
            if job is None:
                return
            job.state = JobState.FAILED
            job.finished_at = utcnow()
            job.message = f"Validation failed: {exc}"
            session.add(job)
            session.commit()
        return

    with Session(engine) as session:
        job = session.get(ValidationJob, job_id)
        if job is None:
            return
        runs = session.exec(select(ValidationRun).where(ValidationRun.job_id == job_id)).all()
        job.valid_count = sum(1 for run in runs if run.raw_status == CredentialStatus.VALID.value)
        job.invalid_count = sum(1 for run in runs if run.raw_status == CredentialStatus.INVALID.value)
        job.error_count = sum(1 for run in runs if run.raw_status == CredentialStatus.ERROR.value)
        job.checked = len(runs)
        job.providers_affected = len({run.provider_id for run in runs})
        job.state = JobState.COMPLETE
        job.finished_at = utcnow()
        job.message = "Validation complete"
        session.add(job)
        session.commit()


async def _validate_one(
    job_id: int,
    credential_id: int,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        with Session(engine) as session:
            credential = session.get(Credential, credential_id)
            provider = session.get(Provider, credential.provider_id) if credential else None
            if credential is None or provider is None:
                return

        outcome = await validate_credential(client, provider, credential)
        settings = get_settings()
        for attempt in range(1, settings.validation_retries):
            if outcome.status != CredentialStatus.ERROR:
                break
            await asyncio.sleep(0.5)
            outcome = await validate_credential(client, provider, credential)

        with Session(engine) as session:
            credential = session.get(Credential, credential_id)
            provider = session.get(Provider, credential.provider_id) if credential else None
            job = session.get(ValidationJob, job_id)
            if credential is None or provider is None or job is None:
                return

            credential.status = outcome.status
            credential.last_checked_at = utcnow()
            credential.updated_at = utcnow()
            credential.account_metadata = outcome.account_metadata or {}
            credential.expires_at = outcome.expires_at
            session.add(credential)

            session.add(
                ValidationRun(
                    job_id=job_id,
                    credential_id=credential.id,
                    provider_id=provider.id,
                    method=outcome.method,
                    success=outcome.success,
                    http_status=outcome.http_status,
                    message=outcome.message,
                    raw_status=outcome.status.value,
                )
            )

            job.checked += 1
            if outcome.status == CredentialStatus.VALID:
                job.valid_count += 1
            elif outcome.status == CredentialStatus.INVALID:
                job.invalid_count += 1
            else:
                job.error_count += 1
            session.add(job)
            session.commit()


async def validate_credential(
    client: httpx.AsyncClient,
    provider: Provider,
    credential: Credential,
) -> ValidationOutcome:
    api_outcome = await _validate_with_xtream_api(client, provider, credential)
    if api_outcome.status in {CredentialStatus.VALID, CredentialStatus.INVALID}:
        return api_outcome
    return await _validate_with_playlist_fetch(client, credential, api_outcome.message)


async def _validate_with_xtream_api(
    client: httpx.AsyncClient,
    provider: Provider,
    credential: Credential,
) -> ValidationOutcome:
    url = f"{provider.base_url}/player_api.php?{urlencode({'username': credential.username, 'password': credential.password})}"
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return ValidationOutcome(
            status=CredentialStatus.ERROR,
            method="xtream_api",
            success=False,
            http_status=None,
            message=f"Xtream API request failed: {exc}",
        )

    if response.status_code in {401, 403}:
        return ValidationOutcome(
            status=CredentialStatus.INVALID,
            method="xtream_api",
            success=False,
            http_status=response.status_code,
            message="Provider rejected credentials",
        )

    if response.status_code >= 500:
        return ValidationOutcome(
            status=CredentialStatus.ERROR,
            method="xtream_api",
            success=False,
            http_status=response.status_code,
            message="Provider API server error",
        )

    try:
        payload = response.json()
    except ValueError:
        return ValidationOutcome(
            status=CredentialStatus.ERROR,
            method="xtream_api",
            success=False,
            http_status=response.status_code,
            message="Provider API did not return JSON",
        )

    user_info = payload.get("user_info") if isinstance(payload, dict) else None
    if not isinstance(user_info, dict):
        return ValidationOutcome(
            status=CredentialStatus.ERROR,
            method="xtream_api",
            success=False,
            http_status=response.status_code,
            message="Provider API response did not include account info",
            account_metadata=payload if isinstance(payload, dict) else {},
        )

    account_status = str(user_info.get("status", "")).lower()
    auth = str(user_info.get("auth", "")).lower()
    is_valid = account_status == "active" or auth in {"1", "true"}
    is_invalid = account_status in {"disabled", "banned", "expired"} or auth in {"0", "false"}
    expires_at = _parse_expiry(user_info.get("exp_date"))

    if is_valid:
        return ValidationOutcome(
            status=CredentialStatus.VALID,
            method="xtream_api",
            success=True,
            http_status=response.status_code,
            message="Account is valid",
            account_metadata=user_info,
            expires_at=expires_at,
        )
    if is_invalid:
        return ValidationOutcome(
            status=CredentialStatus.INVALID,
            method="xtream_api",
            success=False,
            http_status=response.status_code,
            message=f"Account is {account_status or 'not authorized'}",
            account_metadata=user_info,
            expires_at=expires_at,
        )

    return ValidationOutcome(
        status=CredentialStatus.ERROR,
        method="xtream_api",
        success=False,
        http_status=response.status_code,
        message="Provider API account status was inconclusive",
        account_metadata=user_info,
        expires_at=expires_at,
    )


async def _validate_with_playlist_fetch(
    client: httpx.AsyncClient,
    credential: Credential,
    previous_message: str,
) -> ValidationOutcome:
    try:
        response = await client.get(credential.source_url)
    except httpx.HTTPError as exc:
        return ValidationOutcome(
            status=CredentialStatus.ERROR,
            method="playlist_fetch",
            success=False,
            http_status=None,
            message=f"{previous_message}; playlist request failed: {exc}",
        )

    body_start = response.text[:512].lower()
    if response.status_code == 200 and ("#extm3u" in body_start or "#extinf" in body_start):
        return ValidationOutcome(
            status=CredentialStatus.VALID,
            method="playlist_fetch",
            success=True,
            http_status=response.status_code,
            message="Playlist fetch returned M3U content",
        )

    if response.status_code in {401, 403, 404, 410}:
        return ValidationOutcome(
            status=CredentialStatus.INVALID,
            method="playlist_fetch",
            success=False,
            http_status=response.status_code,
            message="Playlist request rejected credentials or link no longer exists",
        )

    return ValidationOutcome(
        status=CredentialStatus.ERROR,
        method="playlist_fetch",
        success=False,
        http_status=response.status_code,
        message="Playlist fetch did not confirm credential validity",
    )


def _parse_expiry(value: Any) -> datetime | None:
    if value in {None, "", "null"}:
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)
