import pytest

from app.parser import parse_m3u_line, parse_m3u_lines


def test_parse_valid_xtream_link_with_explicit_port() -> None:
    parsed = parse_m3u_line(
        "http://Example.COM:8080/get.php?username=alice&password=secret&type=m3u_plus&output=ts"
    )

    assert parsed.scheme == "http"
    assert parsed.host == "example.com"
    assert parsed.port == 8080
    assert parsed.base_url == "http://example.com:8080"
    assert parsed.username == "alice"
    assert parsed.password == "secret"


def test_parse_valid_xtream_link_with_default_https_port() -> None:
    parsed = parse_m3u_line("https://provider.test/get.php?username=bob&password=pass")

    assert parsed.port == 443
    assert parsed.base_url == "https://provider.test:443"


@pytest.mark.parametrize(
    "line",
    [
        "not a url",
        "ftp://provider.test/get.php?username=a&password=b",
        "http://provider.test/get.php?username=a",
        "http://provider.test/get.php?password=b",
    ],
)
def test_parse_rejects_malformed_or_incomplete_lines(line: str) -> None:
    with pytest.raises(ValueError):
        parse_m3u_line(line)


def test_parse_lines_skips_comments_and_collects_errors() -> None:
    parsed, errors = parse_m3u_lines(
        "\n".join(
            [
                "#EXTM3U",
                "http://provider.test/get.php?username=a&password=b",
                "bad",
            ]
        )
    )

    assert len(parsed) == 1
    assert len(errors) == 1
    assert errors[0].line_number == 3
