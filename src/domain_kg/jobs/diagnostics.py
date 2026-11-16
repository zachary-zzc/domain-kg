"""Allowlisted user-facing failures; raw provider and source text never enters receipts."""

from domain_kg.errors import ErrorCode

FAILURES = {
    "PROVIDER_AUTHENTICATION_FAILED": (
        "The model API key was rejected.",
        "Open Advanced settings, correct the key and run the Hello "
        "connection test before retrying.",
    ),
    "PROVIDER_BILLING_REQUIRED": (
        "The model provider reports a billing problem.",
        "Check account credits and payment status in the provider console, "
        "then test the connection again.",
    ),
    "PROVIDER_QUOTA_EXCEEDED": (
        "The model provider allowance is exhausted.",
        "Check provider credits, quota and spending limits. "
        "This differs from the local daily budget.",
    ),
    "PROVIDER_RATE_LIMITED": (
        "The model provider is rate limiting requests.",
        "Wait for the provider limit to recover before retrying this job.",
    ),
    "PROVIDER_MODEL_NOT_FOUND": (
        "The selected model or API route was not found.",
        "Open Advanced settings, check the model name and base URL, then run the connection test.",
    ),
    "QUOTA_EXCEEDED": (
        "A service or model quota was reached.",
        "Ask the owner to check parser limits, model budgets and storage quotas before retrying.",
    ),
    "UNSUPPORTED_FORMAT": (
        "The parser could not read this file format.",
        "Check that the file opens locally. Export legacy DOC to DOCX or "
        "PDF; scanned PDFs need OCR.",
    ),
    "VALIDATION_ERROR": (
        "The file or its contents did not pass validation.",
        "Check for an encrypted, damaged or oversized file. Correct it "
        "and review the folder again.",
    ),
    "EVIDENCE_VALIDATION_FAILED": (
        "Extracted claims could not be verified against the source.",
        "Check the source and extraction model settings before retrying. "
        "No unverified graph was published.",
    ),
    "PROVIDER_UNAVAILABLE": (
        "The configured model service could not complete processing.",
        "Check model settings and provider availability, then retry this job.",
    ),
    "VERSION_CONFLICT": (
        "The source changed while this job was processing.",
        "Review the folder again to compare with the current published version.",
    ),
    "REVISION_NOT_READY": (
        "The new graph revision is not ready to publish.",
        "Inspect graph and vector readiness in Maintenance before retrying.",
    ),
    "PROJECTION_INCONSISTENT": (
        "The derived indexes do not match the accepted data.",
        "Ask the owner to inspect and repair the indexes in Maintenance.",
    ),
    "RETRY_EXHAUSTED": (
        "Automatic recovery attempts were exhausted.",
        "Check service health before explicitly retrying this job.",
    ),
}
DEFAULT_FAILURE = (
    "Processing could not complete.",
    "Check service health and current access, then retry. Give the "
    "owner the job and diagnostic references.",
)
PARSER_LIMIT_ACTION = (
    "Open Advanced settings > Document parsing to review memory and timeout limits, then retry. "
    "Split or simplify the file if it still exceeds the limits."
)
OCR_SETUP_ACTION = (
    "Install or repair local Tesseract and its English and simplified Chinese language data, "
    "then retry this file."
)
MODEL_CONTEXT_ACTION = "Split the source or choose a model with a larger context, then retry."
DETAIL_CODES = ("QUOTA_EXCEEDED", "EVIDENCE_VALIDATION_FAILED", "PROVIDER_UNAVAILABLE")
SAFE_DETAILS = {
    "The extraction request timed out.": (
        "Open Advanced settings and check the model timeout and provider availability, "
        "then retry this file."
    ),
    "The extraction model could not be reached.": (
        "Check the configured API URL, network connection and provider availability, then retry."
    ),
    "The extraction model returned an invalid structured response.": (
        "Check that the selected model supports the configured JSON output format, "
        "then run the connection test and retry."
    ),
    "The extraction response was blocked or contained unsupported content.": (
        "Check the model's content policy and supported output format, "
        "then retry or choose another approved model."
    ),
    "The model returned unsupported content.": (
        "Check the model's content policy and supported output format, "
        "then retry or choose another approved model."
    ),
    "The provider response exceeded its size limit.": (
        "Check the API format and Maximum output tokens in Advanced settings, then retry."
    ),
    "The approved provider did not return a valid response.": FAILURES["PROVIDER_UNAVAILABLE"][1],
    "The model service could not complete the request. Check its URL, model and availability.": (
        FAILURES["PROVIDER_UNAVAILABLE"][1]
    ),
    (
        "Local PDF OCR requires Tesseract with English and simplified Chinese language data."
    ): OCR_SETUP_ACTION,
    ("Local OCR requires Tesseract with English and simplified Chinese language data."): (
        OCR_SETUP_ACTION
    ),
    "Local OCR failed.": OCR_SETUP_ACTION,
    "Local OCR runtime limit exceeded.": PARSER_LIMIT_ACTION,
    "Local OCR output limit exceeded.": PARSER_LIMIT_ACTION,
    "Local PDF OCR failed.": OCR_SETUP_ACTION,
    "Local PDF OCR runtime limit exceeded.": PARSER_LIMIT_ACTION,
    "Local PDF OCR output limit exceeded.": PARSER_LIMIT_ACTION,
    "Local PDF rendering runtime limit exceeded.": PARSER_LIMIT_ACTION,
    "Local PDF rendering output limit exceeded.": PARSER_LIMIT_ACTION,
    "Local PDF OCR could not render a page.": OCR_SETUP_ACTION,
    "Parser memory limit exceeded.": PARSER_LIMIT_ACTION,
    "Parser runtime limit exceeded.": PARSER_LIMIT_ACTION,
    "Parsed output exceeds the resource limit.": PARSER_LIMIT_ACTION,
    "Expanded document exceeds the parser limit.": PARSER_LIMIT_ACTION,
    "PDF exceeds the 500-page parser limit.": PARSER_LIMIT_ACTION,
    "PDF exceeds the configured page limit.": PARSER_LIMIT_ACTION,
    "Expanded archive exceeds the parser limit.": PARSER_LIMIT_ACTION,
    "DOCX text exceeds the parser limit.": PARSER_LIMIT_ACTION,
    "JSON nesting or pointer limit exceeded.": PARSER_LIMIT_ACTION,
    "Original storage quota exceeded.": "Ask the owner to review original-file storage capacity.",
    "The provider usage budget is exhausted.": FAILURES["QUOTA_EXCEEDED"][1],
    "Embedding context cannot hold source text.": MODEL_CONTEXT_ACTION,
    "Model context cannot hold the schema and one source character.": MODEL_CONTEXT_ACTION,
    "Source exceeds the model-window budget.": MODEL_CONTEXT_ACTION,
    "Source exceeds the selected extraction context; split it first.": MODEL_CONTEXT_ACTION,
    "Extraction exceeded its output budget.": (
        "Open Advanced settings and increase Maximum output tokens, or reduce the chat context "
        "to produce smaller source windows, then retry. The source was already split twice."
    ),
}
EVIDENCE_DETAILS = {
    "An evidence quotation or location does not match the original source.",
    "The model returned duplicate entity identifiers.",
    "An extracted entity uses an unsupported type.",
    "An extracted entity name is absent from its cited source passages.",
    "An entity description, alias or identifier is not grounded in its evidence.",
    "An extracted relation refers to an unknown entity.",
    "An extracted relation uses an unsupported predicate or endpoint type.",
    "The relation evidence does not contain both endpoint names.",
    "A positive relation cites source text containing negation.",
    "An extracted qualifier is absent from its cited source passages.",
    "The model returned invalid or repeated source passage references.",
    "The model returned duplicate entities.",
    "The model returned ambiguous entity names.",
    "An extracted relation refers to an unknown or ambiguous entity.",
}
SAFE_DETAILS.update(
    {message: FAILURES["EVIDENCE_VALIDATION_FAILED"][1] for message in EVIDENCE_DETAILS}
)


def failure_copy(code: str, detail: str | None = None) -> tuple[str, str]:
    if code in DETAIL_CODES and detail in SAFE_DETAILS:
        return detail, SAFE_DETAILS[detail]
    if code in ("FORBIDDEN", "UNAUTHENTICATED", "POLICY_CHANGED", "RESOURCE_NOT_ACCESSIBLE"):
        return (
            "Access changed or the session expired.",
            "Sign in again and confirm access to this source before retrying.",
        )
    return FAILURES.get(code, DEFAULT_FAILURE)


def safe_failure_code(code: str) -> str:
    return (
        code
        if code in {str(value) for value in ErrorCode} | {"RETRY_EXHAUSTED"}
        else "BACKEND_UNAVAILABLE"
    )
