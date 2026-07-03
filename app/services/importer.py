from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import Credential, CredentialStatus, Provider, utcnow
from app.parser import ParseError, parse_m3u_lines


@dataclass
class ImportSummary:
    parsed: int = 0
    providers_created: int = 0
    providers_updated: int = 0
    credentials_created: int = 0
    credentials_updated: int = 0
    errors: list[ParseError] = field(default_factory=list)


def import_m3u_text(session: Session, text: str) -> ImportSummary:
    links, errors = parse_m3u_lines(text)
    summary = ImportSummary(parsed=len(links), errors=errors)

    for link in links:
        provider = session.exec(
            select(Provider).where(
                Provider.scheme == link.scheme,
                Provider.host == link.host,
                Provider.port == link.port,
            )
        ).first()

        now = utcnow()
        if provider is None:
            provider = Provider(
                scheme=link.scheme,
                host=link.host,
                port=link.port,
                base_url=link.base_url,
                created_at=now,
                updated_at=now,
            )
            session.add(provider)
            session.flush()
            summary.providers_created += 1
        else:
            provider.base_url = link.base_url
            provider.updated_at = now
            provider.is_archived = False
            session.add(provider)
            summary.providers_updated += 1

        credential = session.exec(
            select(Credential).where(
                Credential.provider_id == provider.id,
                Credential.username == link.username,
                Credential.password == link.password,
            )
        ).first()

        if credential is None:
            credential = Credential(
                provider_id=provider.id,
                username=link.username,
                password=link.password,
                source_url=link.source_url,
                status=CredentialStatus.UNTESTED,
                created_at=now,
                updated_at=now,
            )
            session.add(credential)
            summary.credentials_created += 1
        else:
            credential.source_url = link.source_url
            credential.updated_at = now
            credential.is_archived = False
            session.add(credential)
            summary.credentials_updated += 1

    session.commit()
    return summary
