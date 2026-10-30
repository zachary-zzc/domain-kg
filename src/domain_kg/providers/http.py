"""Bounded provider JSON and allowlisted account failures, without raw response leakage."""

import json

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.providers.profiles import ProviderConnection

JSON_OBJECT = TypeAdapter(dict[str, object])


class WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


def provider_headers(connection: ProviderConnection) -> dict[str, str]:
    key = connection.api_key.get_secret_value()
    if connection.api_format == "anthropic":
        return {"anthropic-version": "2023-06-01", **({"x-api-key": key} if key else {})}
    return {"Authorization": "Bearer " + key} if key else {}


def provider_failure(status: int, raw: dict[str, object]) -> DomainError:
    nested = raw.get("error")
    values = nested if isinstance(nested, dict) else raw
    codes = {str(values.get(field, "")).lower() for field in ("code", "type")}
    if status == 401:
        return DomainError(
            ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
            "The API key was rejected. Check or replace the key, then test again.",
        )
    if (
        status == 402
        or codes
        & {
            "billing_error",
            "arrearage",
            "insufficientbalance",
            "prepaidbilloverdue",
            "postpaidbilloverdue",
            "billing_not_active",
            "billing_hard_limit_reached",
        }
        or (status == 400 and "credit balance is too low" in str(values.get("message", "")).lower())
    ):
        return DomainError(
            ErrorCode.PROVIDER_BILLING_REQUIRED,
            "The provider reports a billing problem. Check credits and "
            "payment status in its console.",
        )
    if codes & {"insufficient_quota", "budgetlimitexceeded", "usage_limit_reached"}:
        return DomainError(
            ErrorCode.PROVIDER_QUOTA_EXCEEDED,
            "The provider allowance is exhausted. Check its account credits, "
            "quota and spending limits.",
        )
    if status == 429:
        return DomainError(
            ErrorCode.PROVIDER_RATE_LIMITED,
            "The provider is rate limiting requests. Wait before trying again.",
            retryable=True,
        )
    if status == 404:
        return DomainError(
            ErrorCode.PROVIDER_MODEL_NOT_FOUND,
            "The model or API route was not found. Check the model name and base URL.",
        )
    return DomainError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "The model service could not complete the request. Check its URL, model and availability.",
        retryable=status >= 500,
    )


async def provider_json(response: httpx.Response, *, limit: int) -> dict[str, object]:
    body = bytearray()
    bound = limit if response.status_code == 200 else min(limit, 65536)
    async for part in response.aiter_bytes():
        body.extend(part)
        if len(body) > bound:
            if response.status_code != 200:
                raise provider_failure(response.status_code, {})
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "The provider response exceeded its size limit."
            )
    if response.status_code != 200:
        try:
            raw = JSON_OBJECT.validate_json(body)
        except ValueError:
            raw = {}
        raise provider_failure(response.status_code, raw)
    # Numeric and content invariants are checked by the consuming wire model.
    return JSON_OBJECT.validate_python(json.loads(body))
