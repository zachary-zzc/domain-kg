"""Locale selection is isolated, non-authoritative, and preserves evidence."""

import asyncio

import httpx
from fastapi import FastAPI

from domain_kg.console.i18n import ConsoleLocaleMiddleware, language_router, translate
from domain_kg.console.rendering import html


def application():
    app = FastAPI()
    app.add_middleware(ConsoleLocaleMiddleware)
    app.include_router(language_router(), prefix="/admin")

    @app.get("/admin")
    async def landing():
        await asyncio.sleep(0)
        return html(
            "catalog.html",
            {
                "title": "Knowledge graphs",
                "catalog": {
                    "items": [
                        {
                            "id": "test",
                            "name": "<b>Search</b>",
                            "state": "ready",
                            "published_revision": 1,
                        }
                    ],
                    "next_cursor": None,
                },
            },
        )

    return app


async def test_both_languages_isolation_and_original_data():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as client:
        english, chinese = await asyncio.gather(
            client.get("/admin", headers={"accept-language": "en"}),
            client.get("/admin", headers={"accept-language": "zh-CN, en;q=0.5"}),
        )
        assert "Knowledge graphs" in english.text
        assert "知识图谱" in chinese.text and '<html lang="zh-CN"' in chinese.text
        assert english.headers["content-language"] == "en"
        assert chinese.headers["content-language"] == "zh-CN"
        for page in (english, chinese):
            assert "&lt;b&gt;Search&lt;/b&gt;" in page.text
            assert page.headers["cache-control"] == "no-store"
            assert "frame-ancestors" in page.headers["content-security-policy"]
        assert translate("Search") == "Search"  # context reset after each request


async def test_language_cookie_switch_and_safe_return():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as client:
        result = await client.get("/admin/language/zh-CN", params={"next": "/admin?q=a&mode=fast"})
        assert result.status_code == 303
        assert result.headers["location"] == "/admin?q=a&mode=fast"
        assert "HttpOnly" in result.headers["set-cookie"]
        assert "知识图谱" in (await client.get("/admin")).text
        assert (await client.get("/admin/language/fr")).status_code == 422
        for target in (
            "https://evil.test",
            "//evil.test",
            "/admin\\evil",
            "/admin/../api/v1",
            "/administrator",
            "/admin%2f%2fexample",
            "/admin\r\nLocation: https://evil.test",
        ):
            response = await client.get("/admin/language/en", params={"next": target})
            assert response.status_code == 303
            assert response.headers["location"] == "/admin"
        assert "Knowledge graphs" in (await client.get("/admin")).text


def test_every_template_compiles_and_literal_translations_exist():
    from jinja2 import nodes

    from domain_kg.console.i18n import ZH
    from domain_kg.console.rendering import TEMPLATES

    for name in TEMPLATES.list_templates():
        source, _, _ = TEMPLATES.loader.get_source(TEMPLATES, name)
        parsed = TEMPLATES.parse(source)
        TEMPLATES.get_template(name)
        for call in parsed.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name) and call.node.name == "_":
                if isinstance(call.args[0], nodes.Const):
                    assert call.args[0].value in ZH, (name, call.args[0].value)
    assert all(
        isinstance(key, str) and isinstance(value, str) and value for key, value in ZH.items()
    )


async def test_header_weight_cookie_fallback_and_api_unchanged():
    app = application()

    @app.get("/api/v1/test")
    async def api():
        return {"name": "Search", "label": translate("Search")}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        for header, expected in [
            ("zh;q=0,en;q=0.9", "en"),
            ("zh;q=0.9,en;q=1", "en"),
            ("fr,zh;q=bad,en;q=0.1", "en"),
            ("zh-TW", "zh-CN"),
        ]:
            response = await client.get("/admin", headers={"accept-language": header})
            assert response.headers["content-language"] == expected
        result = await client.get("/admin/language/zh-CN")
        assert "Secure" in result.headers["set-cookie"]
        assert (await client.get("/admin", headers={"accept-language": "en"})).headers[
            "content-language"
        ] == "zh-CN"
        api_result = await client.get("/api/v1/test")
        assert api_result.json() == {"name": "Search", "label": "Search"}
        assert "content-language" not in api_result.headers


def test_data_titles_errors_and_machine_values_are_preserved():
    from domain_kg.console.i18n import LANGUAGE, label
    from domain_kg.console.rendering import TEMPLATES, error_page
    from domain_kg.errors import DomainError, ErrorCode

    token = LANGUAGE.set("zh-CN")
    try:
        kg = html(
            "kg.html",
            {
                "title": "Search",
                "can_owner": False,
                "kg": {"id": "test", "state": "ready", "published_revision": 1},
            },
        )
        assert "<h1>Search</h1>" in kg.body.decode()
        failure = error_page(DomainError(ErrorCode.FORBIDDEN, "<script>safe detail</script>"), 403)
        assert "当前身份无权" in failure.body.decode()
        assert "&lt;script&gt;" in failure.body.decode()
        assert failure.status_code == 403
        assert label("unknown_warning") == "unknown warning"
        # Translating an implicit option must not change the command sent by the browser.
        source, _, _ = TEMPLATES.loader.get_source(TEMPLATES, "curation_form.html")
        for value in ("positive", "negative", "uncertain"):
            assert f'<option value="{value}">' in source
    finally:
        LANGUAGE.reset(token)


def test_error_language_links_do_not_reflect_denied_path_or_query():
    from domain_kg.console.i18n import RETURN_TO
    from domain_kg.console.rendering import error_page
    from domain_kg.errors import DomainError, ErrorCode

    token = RETURN_TO.set("/admin/kgs/denied-private-id/search?q=private-query")
    try:
        denied = error_page(DomainError(ErrorCode.FORBIDDEN, "No access."), 403).body.decode()
        assert "denied-private-id" not in denied
        assert "private-query" not in denied
    finally:
        RETURN_TO.reset(token)
