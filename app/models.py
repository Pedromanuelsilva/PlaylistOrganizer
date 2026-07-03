from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CredentialStatus(StrEnum):
    UNTESTED = "untested"
    VALID = "valid"
    INVALID = "invalid"
    ERROR = "error"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class Provider(SQLModel, table=True):
    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("scheme", "host", "port", name="uq_provider_identity"),)

    id: int | None = Field(default=None, primary_key=True)
    scheme: str = Field(index=True)
    host: str = Field(index=True)
    port: int = Field(index=True)
    base_url: str
    is_archived: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Credential(SQLModel, table=True):
    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("provider_id", "username", "password", name="uq_credential_identity"),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="providers.id", index=True)
    username: str = Field(index=True)
    password: str
    source_url: str
    status: CredentialStatus = Field(default=CredentialStatus.UNTESTED, index=True)
    last_checked_at: datetime | None = None
    expires_at: datetime | None = None
    account_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    is_archived: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ValidationJob(SQLModel, table=True):
    __tablename__ = "validation_jobs"

    id: int | None = Field(default=None, primary_key=True)
    job_type: str = Field(index=True)
    state: JobState = Field(default=JobState.QUEUED, index=True)
    total: int = 0
    checked: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    error_count: int = 0
    providers_affected: int = 0
    message: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ValidationRun(SQLModel, table=True):
    __tablename__ = "validation_runs"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="validation_jobs.id", index=True)
    credential_id: int = Field(foreign_key="credentials.id", index=True)
    provider_id: int = Field(foreign_key="providers.id", index=True)
    method: str
    success: bool
    http_status: int | None = None
    message: str = ""
    raw_status: str = ""
    checked_at: datetime = Field(default_factory=utcnow)
