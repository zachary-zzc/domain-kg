"""Browser forms are bounded scalar input; ambiguous security fields are rejected."""

import pytest

from domain_kg.console.forms import decode_form
from domain_kg.errors import DomainError


def test_form_parser_preserves_text_for_template_escaping_and_explicit_clear():
    assert decode_form(
        b"title=Hello+%3Cscript%3E&description=&csrf_token=opaque",
        "application/x-www-form-urlencoded; charset=UTF-8",
    ) == {"title": "Hello <script>", "description": "", "csrf_token": "opaque"}


@pytest.mark.parametrize(
    "body,media_type",
    [
        (b"csrf_token=first&csrf_token=second", "application/x-www-form-urlencoded"),
        (b"title=private&title=other", "application/x-www-form-urlencoded"),
        (b"title=%FF", "application/x-www-form-urlencoded"),
        (b"title=%GG", "application/x-www-form-urlencoded"),
        (b"title=hello%00world", "application/x-www-form-urlencoded"),
        (b"=value", "application/x-www-form-urlencoded"),
        (b"title", "application/x-www-form-urlencoded"),
        (b"a=" + b"x" * 65536, "application/x-www-form-urlencoded"),
        (b"title=private", "application/json"),
    ],
)
def test_form_parser_denies_ambiguous_oversize_or_invalid_input_without_echo(body, media_type):
    with pytest.raises(DomainError) as error:
        decode_form(body, media_type)
    assert "private" not in error.value.message and "csrf_token=" not in error.value.message


def test_form_parser_limits_field_count():
    with pytest.raises(DomainError):
        decode_form(
            b"&".join(f"f{index}=value".encode() for index in range(101)),
            "application/x-www-form-urlencoded",
        )
