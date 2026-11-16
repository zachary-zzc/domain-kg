"""Cursor errors offer current lists, never reflected denied or arbitrary targets."""

from html import unescape
from urllib.parse import urlencode
from uuid import UUID

import httpx
import pytest
from fastapi import Request
from fastapi.routing import APIRoute

from domain_kg.app import create_app
from domain_kg.console.i18n import RETURN_TO
from domain_kg.console.rendering import error_page
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.settings import Settings

KG = UUID(int=1)


def request(path, *, query=None, method="GET"):
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "http",
            "server": ("localhost", 8000),
            "path": path,
            "query_string": urlencode(query or {}).encode(),
            "headers": [],
        }
    )


@pytest.mark.parametrize(
    ("collection", "view"),
    [("materials", "published"), ("materials", "removed"), ("jobs", "attention")],
)
def test_expired_cursor_links_to_first_page_with_only_valid_list_context(collection, view):
    path = f"/admin/kgs/{KG}/{collection}"
    target = request(
        path,
        query={
            "view": view,
            "limit": 10,
            "include_unverified": "true",
            "cursor": "expired-secret-token",
            "q": "private-query",
            "return_url": "https://evil.test",
        },
    )
    response = error_page(
        DomainError(ErrorCode.CURSOR_EXPIRED, "The list cursor scope changed."),
        409,
        request=target,
    )
    content = unescape(response.body.decode())
    expected = f'{path}?view={view}&limit=10'
    if collection == "materials":
        expected += "&include_unverified=true"
    assert f'href="{expected}"' in content
    assert "Restart this list from the first page" in content
    for private in ("expired-secret-token", "private-query", "evil.test"):
        assert private not in content
    assert response.status_code == 409
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.RESOURCE_NOT_ACCESSIBLE,
        ErrorCode.POLICY_CHANGED,
        ErrorCode.VERSION_CONFLICT,
    ],
)
def test_other_errors_do_not_reveal_the_requested_kg_or_query(code):
    path = f"/admin/kgs/{KG}/materials"
    token = RETURN_TO.set(path + "?cursor=private-token")
    try:
        response = error_page(
            DomainError(code, "The request could not be completed."),
            409,
            request=request(path, query={"cursor": "private-token"}),
        )
    finally:
        RETURN_TO.reset(token)
    content = response.body.decode()
    assert str(KG) not in content
    assert "private-token" not in content
    assert "Restart this list" not in content
    assert 'href="/admin"' in content


@pytest.mark.parametrize(
    ("path", "query", "method"),
    [
        (f"/admin/kgs/{KG}/materials/child", {}, "GET"),
        ("/admin/kgs/not-a-uuid/materials", {}, "GET"),
        (f"/admin/kgs/{KG}/materials", {"view": "https://evil.test"}, "GET"),
        (f"/admin/kgs/{KG}/materials", {"limit": 101}, "GET"),
        (f"/admin/kgs/{KG}/materials", {"include_unverified": "invalid"}, "GET"),
        (f"/admin/kgs/{KG}/materials", {}, "POST"),
    ],
)
def test_cursor_recovery_does_not_turn_invalid_context_into_a_link(path, query, method):
    response = error_page(
        DomainError(ErrorCode.CURSOR_EXPIRED, "The list cursor scope changed."),
        409,
        request=request(path, query=query, method=method),
    )
    assert "Restart this list" not in response.body.decode()


async def test_http_cursor_handler_adds_explicit_html_recovery_without_changing_json_errors():
    app = create_app(Settings(oidc_client_id=""))

    async def expired_cursor():
        raise DomainError(ErrorCode.CURSOR_EXPIRED, "The material cursor scope changed.")

    path = f"/admin/kgs/{KG}/materials"
    app.router.routes.insert(0, APIRoute(path, expired_cursor, methods=["GET"]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        params = {"view": "published", "limit": 10, "cursor": "expired-token"}
        browser = await client.get(path, params=params)
        assert browser.status_code == 409
        assert (
            f'href="{path}?view=published&limit=10&include_unverified=false"'
            in unescape(browser.text)
        )
        assert "expired-token" not in browser.text
        api = await client.get(path, params=params, headers={"accept": "application/json"})
        assert api.status_code == 409 and api.json()["code"] == "CURSOR_EXPIRED"
        assert "Restart this list" not in api.text
