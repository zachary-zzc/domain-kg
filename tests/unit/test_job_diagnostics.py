from uuid import uuid4

from domain_kg.jobs.queue import JobQueue


def test_failure_has_safe_explanation_and_correlation_without_exception_text():
    request = uuid4()
    result = JobQueue._receipt(
        {
            "org_id": str(uuid4()),
            "kg_id": str(uuid4()),
            "id": str(uuid4()),
            "stage": "failed",
            "error_code": "QUOTA_EXCEEDED",
            "failure_stage": "extracting",
            "error_request_id": str(request),
            "error_message": "secret backend response",
        }
    )
    assert result.error.code == "QUOTA_EXCEEDED"
    assert result.error.stage == "extracting"
    assert result.error.request_id == request
    assert "budget" in result.error.action.lower()
    assert "secret backend response" not in result.model_dump_json()


def test_success_never_displays_stale_failure_from_a_previous_attempt():
    result = JobQueue._receipt(
        {
            "org_id": str(uuid4()),
            "kg_id": str(uuid4()),
            "id": str(uuid4()),
            "stage": "ready",
            "error_code": "BACKEND_UNAVAILABLE",
        }
    )
    assert result.error is None
    assert "BACKEND_UNAVAILABLE" not in result.warnings


def test_parser_limit_is_distinguished_from_a_model_billing_budget():
    result = JobQueue._receipt(
        {
            "org_id": str(uuid4()),
            "kg_id": str(uuid4()),
            "id": str(uuid4()),
            "stage": "failed",
            "error_code": "QUOTA_EXCEEDED",
            "failure_stage": "parsing",
            "error_detail": "Parser runtime limit exceeded.",
        }
    )
    assert result.error.message == "Parser runtime limit exceeded."
    assert "split" in result.error.action.lower()
    assert "budget" not in result.error.action.lower()


def test_evidence_failure_keeps_only_allowlisted_specific_diagnostics():
    from domain_kg.jobs.diagnostics import failure_copy

    message = "An extracted entity name is absent from its cited source passages."
    assert failure_copy("EVIDENCE_VALIDATION_FAILED", message)[0] == message
    assert "private" not in str(failure_copy("EVIDENCE_VALIDATION_FAILED", "private source text"))


def test_output_truncation_explains_the_model_output_setting():
    from domain_kg.jobs.diagnostics import failure_copy

    message = "Extraction exceeded its output budget."
    summary, action = failure_copy("PROVIDER_UNAVAILABLE", message)
    assert summary == message
    assert "output" in action.lower()
    assert "Advanced settings" in action
    assert "private" not in str(failure_copy("PROVIDER_UNAVAILABLE", "private model response"))
