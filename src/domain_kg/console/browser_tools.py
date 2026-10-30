"""Browser workflows over the existing upload, version, search and MCP APIs."""

from typing import get_args
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from domain_kg.console.i18n import label, translate
from domain_kg.console.rendering import browser_session, page
from domain_kg.errors import ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.diagnostics import DEFAULT_FAILURE, FAILURES, SAFE_DETAILS, failure_copy
from domain_kg.jobs.models import JobStage
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime

FOLDER_COPY = (
    "No folder selected.",
    "Checking files",
    "Review complete",
    "Uploading files",
    "Uploads paused",
    "Uploads complete",
    "Published",
    "Failed",
    "Waiting",
    "Skipped",
    "Invalid",
    "Uploading original",
    "Awaiting processing",
    "View job",
    "Retry job",
    "Retry upload",
    "Continue uploads",
    "Refresh status",
    "Add",
    "Replace",
    "Unchanged",
    "Skip",
    "Retry",
    "Track",
    "File progress",
    "Recent import jobs",
    "Ready to upload",
    "Choose a folder and review its files before uploading.",
    "Files already submitted continue in the background. Unuploaded "
    "files need this tab or folder reselection.",
    "All files have reached a final state. Review any failures below.",
    "Some files could not be uploaded. Retry them below; accepted jobs are preserved.",
    "No files need uploading. Review skipped files or follow existing jobs below.",
    "Status could not be refreshed. Accepted jobs continue; use Refresh status to reconnect.",
    "Review failed. Check access and retry; no files were submitted.",
    "Sign in again before continuing. Source details were hidden "
    "because access could not be verified.",
    "Upload",
    "Processing",
    "Finished",
    "files",
    "of",
    "Errors",
    "bytes",
)
FOLDER_ISSUES = {
    "EMPTY_FILE": "Empty file; skipped.",
    "HIDDEN_FILE": "Hidden file; skipped.",
    "UNSUPPORTED_FILE": "Unsupported format; export to TXT, PDF or DOCX.",
    "FILE_TOO_LARGE": "File exceeds 50 MiB; split it before uploading.",
    "INVALID_PATH": "Invalid, duplicate or excessively long file path.",
    "AMBIGUOUS_SOURCE": "Multiple sources match this path. Resolve them in Sources first.",
    "PENDING_CHANGED": "An earlier job has different contents. Open "
    "its job before replacing this source.",
    "READ_FAILED": "The browser could not read this file. Check access and reselect the folder.",
    "NETWORK_ERROR": "The connection was interrupted. Retry safely with the same file.",
}


def folder_copy() -> dict[str, str]:
    messages = {value: translate(value) for value in FOLDER_COPY}
    for message, action in (*FAILURES.values(), DEFAULT_FAILURE, failure_copy("FORBIDDEN")):
        messages[message] = translate(message)
        messages[action] = translate(action)
    for message, action in SAFE_DETAILS.items():
        messages[message] = translate(message)
        messages[action] = translate(action)
    messages.update({"stage." + stage: label(stage) for stage in get_args(JobStage)})
    messages.update({"issue." + key: translate(value) for key, value in FOLDER_ISSUES.items()})
    messages.update(
        {
            "issue." + code: translate(failure_copy(code)[0])
            + " "
            + translate(failure_copy(code)[1])
            for code in ErrorCode
        }
    )
    return messages


def browser_tools_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    async def render(request: Request, kg: UUID, *, folder: bool) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal,
            (kg,),
            Action.INGEST if folder else Action.READ,
            session.member.org_id,
        )
        return await page(
            runtime,
            session,
            access,
            "folder.html" if folder else "mcp_test.html",
            {
                "title": "Import folder" if folder else "MCP playground",
                "kg": await runtime.catalog.get_kg(access, kg),
                "jobs": await runtime.job_inspection.list(access, kg, PageRequest(limit=10))
                if folder
                else None,
                "folder_copy": folder_copy() if folder else {},
            },
        )

    @router.get("/kgs/{kg_id}/folder")
    async def folder(request: Request, kg_id: UUID) -> HTMLResponse:
        return await render(request, kg_id, folder=True)

    @router.get("/kgs/{kg_id}/mcp-test")
    async def mcp_test(request: Request, kg_id: UUID) -> HTMLResponse:
        return await render(request, kg_id, folder=False)

    return router
