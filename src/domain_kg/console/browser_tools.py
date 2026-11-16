"""Browser workflows over the existing upload, version, search and MCP APIs."""

from typing import get_args
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from domain_kg.console.i18n import label, translate
from domain_kg.console.rendering import browser_session, page
from domain_kg.errors import ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.diagnostics import DEFAULT_FAILURE, FAILURES, SAFE_DETAILS, failure_copy
from domain_kg.jobs.models import JobStage
from domain_kg.transport.runtime import ServiceRuntime

FOLDER_COPY = (
    "Files",
    "Folder",
    "Select",
    "Submitting",
    "Resume file",
    "Current import",
    "Open source",
    "View KG",
    "Upload and construct KG",
    "No files selected.",
    "Choose files or a folder and review before uploading.",
    "Select the same original files above to finish uploading.",
    "This file’s status could not be refreshed. Other files continue; reconnecting automatically.",
    "Published with warnings. Review the job for details.",
    "Processing continues in the background.",
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
    "Choose folder",
    "Choose files",
    "Saved import. Reselect originals only for unfinished uploads.",
    "No files match this view. Change the filter or search.",
    "Included files",
    "Needs attention",
    "All files",
    "Found",
    "Review the included files, then upload. Skipped files are not uploaded.",
    "Each file runs independently. You can leave after uploads finish.",
    "Loading saved import status. Accepted jobs continue in the background.",
)
FOLDER_ISSUES = {
    "IMPORT_TOO_MANY": "Choose up to 100 files per import.",
    "ORIGINAL_REQUIRED": "Select the same original to continue this upload.",
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

    async def render_mcp(request: Request, kg: UUID) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal,
            (kg,),
            Action.READ,
            session.member.org_id,
        )
        return await page(
            runtime,
            session,
            access,
            "mcp_test.html",
            {
                "title": "MCP playground",
                "kg": await runtime.catalog.get_kg(access, kg),
            },
        )

    @router.get("/kgs/{kg_id}/folder")
    async def folder(request: Request, kg_id: UUID) -> RedirectResponse:
        session = await browser_session(runtime, request)
        await runtime.access(session.principal, (kg_id,), Action.INGEST, session.member.org_id)
        return RedirectResponse(
            f"/admin/kgs/{kg_id}/materials?add=folder",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/kgs/{kg_id}/mcp-test")
    async def mcp_test(request: Request, kg_id: UUID) -> HTMLResponse:
        return await render_mcp(request, kg_id)

    return router
