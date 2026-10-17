"""Request-local interface translations; source content never enters this catalogue."""

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, cast
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

Locale = Literal["en", "zh-CN"]
COOKIE = "domain_kg_language"
LANGUAGE: ContextVar[Locale] = ContextVar("console_language", default="en")
RETURN_TO: ContextVar[str] = ContextVar("console_return_to", default="/admin")
ZH = cast(dict[str, str], json.loads((Path(__file__).parent / "locales/zh-CN.json").read_text()))


def translate(message: str) -> str:
    return ZH.get(message, message) if LANGUAGE.get() == "zh-CN" else message


def label(value: object) -> str:
    """Only explicit interface enums, never evidence names or descriptions."""
    text = str(value)
    key = "state." + text
    return (
        ZH.get(key, text.replace("_", " ")) if LANGUAGE.get() == "zh-CN" else text.replace("_", " ")
    )


def select_locale(request: Request) -> Locale:
    preference = request.cookies.get(COOKIE)
    if preference in ("en", "zh-CN"):
        return cast(Locale, preference)
    accepted: list[tuple[float, Locale]] = []
    for item in request.headers.get("accept-language", "")[:1024].split(",")[:20]:
        tag, _, parameter = item.strip().partition(";")
        tag = tag.lower()
        if tag == "en" or tag.startswith("en-"):
            language: Locale = "en"
        elif tag == "zh" or tag.startswith("zh-"):
            language = "zh-CN"
        else:
            continue
        try:
            quality = float(parameter.strip().removeprefix("q=")) if parameter else 1.0
        except ValueError:
            continue
        if 0 < quality <= 1:
            accepted.append((quality, language))
    return max(accepted, key=lambda item: item[0])[1] if accepted else "en"


def local_return(value: str) -> str:
    # Decode before validation to reject encoded controls, separators and dot segments.
    if len(value) > 4096:
        return "/admin"
    decoded = unquote(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        return "/admin"
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return "/admin"
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return "/admin"
    if parsed.path != "/admin" and not parsed.path.startswith("/admin/"):
        return "/admin"
    if any(part in (".", "..", "") for part in parsed.path.split("/")[2:]):
        return "/admin"
    # Nested encoded paths and the preference endpoint are not return destinations.
    if "%" in parsed.path or parsed.path.startswith("/admin/language"):
        return "/admin"
    return value


def language_url(locale: Locale) -> str:
    return f"/admin/language/{locale}?" + urlencode({"next": RETURN_TO.get()})


class ConsoleLocaleMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/admin" or path.startswith("/admin/")):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        language_token = LANGUAGE.set(select_locale(request))
        target_token = RETURN_TO.set(
            local_return(request.url.path + ("?" + request.url.query if request.url.query else ""))
        )
        try:
            await self.app(scope, receive, send)
        finally:
            RETURN_TO.reset(target_token)
            LANGUAGE.reset(language_token)


def language_router() -> APIRouter:
    router = APIRouter()

    @router.get("/language/{locale}", include_in_schema=False)
    async def change_language(
        request: Request, locale: Locale, next: str = "/admin"
    ) -> RedirectResponse:
        response = RedirectResponse(
            local_return(next), status_code=303, headers={"Cache-Control": "no-store"}
        )
        response.set_cookie(
            COOKIE,
            locale,
            max_age=365 * 86400,
            path="/admin",
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="lax",
        )
        return response

    return router
