from sqlmodel import Session, SQLModel, create_engine, select

from app.main import _provider_row
from app.models import Credential, CredentialStatus, Provider, ValidationRun
from app.services.importer import import_m3u_text


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_provider_row_shows_xtream_api_valid_credentials() -> None:
    with make_session() as session:
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        provider = session.exec(select(Provider)).one()
        credential = session.exec(select(Credential)).one()
        credential.status = CredentialStatus.VALID
        session.add(credential)
        session.add(
            ValidationRun(
                credential_id=credential.id,
                provider_id=provider.id,
                method="xtream_api",
                success=True,
                raw_status="valid",
            )
        )
        session.commit()

        row = _provider_row(session, provider)

    assert row["validation_path"] == "Xtream API"
    assert row["method_counts"]["xtream_api"] == 1
    assert row["method_counts"]["playlist_fetch"] == 0


def test_provider_row_shows_direct_m3u_only_credentials() -> None:
    with make_session() as session:
        import_m3u_text(session, "http://one.test/get.php?username=a&password=b")
        provider = session.exec(select(Provider)).one()
        credential = session.exec(select(Credential)).one()
        credential.status = CredentialStatus.VALID
        session.add(credential)
        session.add(
            ValidationRun(
                credential_id=credential.id,
                provider_id=provider.id,
                method="playlist_fetch",
                success=True,
                raw_status="valid",
            )
        )
        session.commit()

        row = _provider_row(session, provider)

    assert row["validation_path"] == "Direct M3U only"
    assert row["method_counts"]["xtream_api"] == 0
    assert row["method_counts"]["playlist_fetch"] == 1
