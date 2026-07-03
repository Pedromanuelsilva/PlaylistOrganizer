import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import JobState, ValidationJob
from app.services import validator


@pytest.mark.asyncio
async def test_validation_job_marks_empty_job_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(validator, "engine", engine)

    with Session(engine) as session:
        job = ValidationJob(job_type="all_active")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    await validator.run_validation_job(job_id, [])

    with Session(engine) as session:
        job = session.exec(select(ValidationJob).where(ValidationJob.id == job_id)).one()

    assert job.state == JobState.COMPLETE
    assert job.message == "Validation complete"
