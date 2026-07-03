from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import Credential, CredentialStatus, Provider, ValidationJob, ValidationRun
from app.services.importer import import_m3u_text


@contextmanager
def make_client() -> Generator[tuple[TestClient, Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    def override_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_core_get_routes_load() -> None:
    with make_client() as (client, session):
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        provider = session.exec(select(Provider)).one()
        job = ValidationJob(job_type="all_active", total=0)
        session.add(job)
        session.commit()
        session.refresh(job)

        routes = [
            "/",
            "/import",
            "/providers",
            f"/providers/{provider.id}",
            "/validation/jobs",
            f"/validation/jobs/{job.id}",
            "/archive",
        ]

        for route in routes:
            response = client.get(route)
            assert response.status_code == 200, route


def test_import_route_creates_records() -> None:
    with make_client() as (client, session):
        response = client.post(
            "/import",
            data={"links": "http://one.test:8080/get.php?username=a&password=b"},
        )

        assert response.status_code == 200
        assert session.exec(select(Provider)).one().base_url == "http://one.test:8080"
        assert session.exec(select(Credential)).one().username == "a"


def test_archive_invalid_from_job_route() -> None:
    with make_client() as (client, session):
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        provider = session.exec(select(Provider)).one()
        credential = session.exec(select(Credential)).one()
        credential.status = CredentialStatus.INVALID
        job = ValidationJob(job_type="all_active", total=1, invalid_count=1)
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(
            ValidationRun(
                job_id=job.id,
                credential_id=credential.id,
                provider_id=provider.id,
                method="xtream_api",
                success=False,
                raw_status=CredentialStatus.INVALID.value,
            )
        )
        session.commit()

        response = client.post(f"/validation/jobs/{job.id}/archive-invalid", follow_redirects=False)
        session.refresh(credential)

        assert response.status_code == 303
        assert credential.is_archived is True


def test_archive_page_restore_and_delete_routes() -> None:
    with make_client() as (client, session):
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        credential = session.exec(select(Credential)).one()
        credential.is_archived = True
        session.add(credential)
        session.commit()

        restore = client.post(f"/archive/credentials/{credential.id}/restore", follow_redirects=False)
        session.refresh(credential)
        delete = client.post(f"/credentials/{credential.id}/archive", follow_redirects=False)
        session.refresh(credential)
        archived_delete = client.post(f"/archive/credentials/{credential.id}/delete", follow_redirects=False)

        assert restore.status_code == 303
        assert delete.status_code == 303
        assert credential.is_archived is True
        assert archived_delete.status_code == 303
        assert session.get(Credential, credential.id) is None
