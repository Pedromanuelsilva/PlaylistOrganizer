from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Credential, Provider, ValidationRun
from app.services.importer import import_m3u_text


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_import_creates_provider_and_credential() -> None:
    with make_session() as session:
        summary = import_m3u_text(session, "http://one.test:8080/get.php?username=a&password=b")

        providers = session.exec(select(Provider)).all()
        credentials = session.exec(select(Credential)).all()

    assert summary.providers_created == 1
    assert summary.credentials_created == 1
    assert providers[0].base_url == "http://one.test:8080"
    assert credentials[0].username == "a"
    assert credentials[0].password == "b"


def test_import_updates_duplicate_credential_without_duplicate_rows() -> None:
    text = "http://one.test:8080/get.php?username=a&password=b"
    with make_session() as session:
        first = import_m3u_text(session, text)
        second = import_m3u_text(session, text)
        providers = session.exec(select(Provider)).all()
        credentials = session.exec(select(Credential)).all()

    assert first.credentials_created == 1
    assert second.credentials_updated == 1
    assert len(providers) == 1
    assert len(credentials) == 1


def test_same_username_with_different_password_is_kept() -> None:
    with make_session() as session:
        import_m3u_text(
            session,
            "\n".join(
                [
                    "http://one.test/get.php?username=a&password=old",
                    "http://one.test/get.php?username=a&password=new",
                ]
            ),
        )
        credentials = session.exec(select(Credential)).all()

    assert len(credentials) == 2


def test_archive_invalid_run_credentials() -> None:
    with make_session() as session:
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        credential = session.exec(select(Credential)).one()
        provider = session.exec(select(Provider)).one()
        session.add(
            ValidationRun(
                job_id=1,
                credential_id=credential.id,
                provider_id=provider.id,
                method="xtream_api",
                success=False,
                raw_status="invalid",
            )
        )
        credential.is_archived = True
        session.add(credential)
        session.commit()

        archived = session.get(Credential, credential.id)

    assert archived is not None
    assert archived.is_archived is True
