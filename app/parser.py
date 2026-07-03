from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ParsedM3ULink:
    source_url: str
    scheme: str
    host: str
    port: int
    base_url: str
    username: str
    password: str


@dataclass(frozen=True)
class ParseError:
    line_number: int
    line: str
    reason: str


def parse_m3u_line(line: str) -> ParsedM3ULink:
    value = line.strip()
    if not value:
        raise ValueError("blank line")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("not an http(s) URL")

    params = parse_qs(parsed.query, keep_blank_values=True)
    username = _first(params, "username")
    password = _first(params, "password")
    if not username or not password:
        raise ValueError("missing username or password")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("missing provider host")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("invalid provider port") from exc

    base_url = f"{parsed.scheme}://{host}:{port}"
    return ParsedM3ULink(
        source_url=value,
        scheme=parsed.scheme,
        host=host,
        port=port,
        base_url=base_url,
        username=username,
        password=password,
    )


def parse_m3u_lines(text: str) -> tuple[list[ParsedM3ULink], list[ParseError]]:
    parsed_links: list[ParsedM3ULink] = []
    errors: list[ParseError] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed_links.append(parse_m3u_line(line))
        except ValueError as exc:
            errors.append(ParseError(line_number=line_number, line=raw_line, reason=str(exc)))
    return parsed_links, errors


def _first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0].strip() if values else ""
