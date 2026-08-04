"""Domain error → HTTP translation.

One place, so routes stay free of ``try/except`` noise and every domain failure gets a
status code that means something. Starlette resolves handlers by walking the exception's
MRO, so registering :class:`RiskPlatformError` last gives a catch-all for anything the
domain raises that is not listed above it.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import (
    AmbiguousProjectError,
    MalformedScheduleFile,
    ParserUnavailable,
    ProjectNotFound,
    QuantEstimateInvalid,
    QuantEstimateLocked,
    RiskPlatformError,
    ScheduleDeleteBlocked,
    ScheduleGateBlocked,
    ScopeDeleteBlocked,
    ScopeInvalid,
    ScopeNotFound,
    SimulationNotAssemblable,
    SimulationRunNotCancellable,
    UnsupportedScheduleFormat,
)


def _payload(error: str, message: str, **extra: object) -> dict:
    return {"error": error, "detail": message, **extra}


async def _ambiguous_project(request: Request, exc: AmbiguousProjectError) -> JSONResponse:
    # 409: the request is valid, it just cannot be resolved without a choice. The client
    # re-sends with a project_id rather than guessing.
    return JSONResponse(
        status_code=409,
        content=_payload(
            "ambiguous_project",
            str(exc),
            projects=[
                {"id": pid, "name": name, "activity_count": count}
                for pid, name, count in exc.candidates
            ],
        ),
    )


async def _project_not_found(request: Request, exc: ProjectNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=_payload("project_not_found", str(exc), available=exc.available),
    )


async def _delete_blocked(
    request: Request, exc: ScheduleDeleteBlocked
) -> JSONResponse:
    # 409, not 403: the request is well formed and the caller is allowed to do it. It
    # needs a confirmation that names what would be lost, which the counts below supply.
    return JSONResponse(
        status_code=409,
        content=_payload(
            "delete_blocked",
            str(exc),
            version_id=exc.version_id,
            accepted_mappings=exc.accepted,
            proposed_mappings=exc.proposed,
        ),
    )


async def _unsupported_format(
    request: Request, exc: UnsupportedScheduleFormat
) -> JSONResponse:
    return JSONResponse(
        status_code=415,
        content=_payload("unsupported_format", str(exc), supported=exc.supported),
    )


async def _parser_unavailable(request: Request, exc: ParserUnavailable) -> JSONResponse:
    # 415 rather than 501: the format is understood, this deployment cannot read it. The
    # reason string tells the user what to do instead.
    return JSONResponse(
        status_code=415,
        content=_payload("parser_unavailable", str(exc), reason=exc.reason),
    )


async def _malformed(request: Request, exc: MalformedScheduleFile) -> JSONResponse:
    return JSONResponse(status_code=422, content=_payload("malformed_file", str(exc)))


async def _quant_invalid(request: Request, exc: QuantEstimateInvalid) -> JSONResponse:
    # 422 rather than 400: the payload parsed cleanly and every field is the right type.
    # What failed is the relationship between them, which is exactly what 422 is for.
    return JSONResponse(
        status_code=422,
        content=_payload("quant_estimate_invalid", str(exc), issues=exc.issues),
    )


async def _quant_locked(request: Request, exc: QuantEstimateLocked) -> JSONResponse:
    # 409: the caller is allowed to do this, but not while a run depends on the numbers.
    return JSONResponse(
        status_code=409,
        content=_payload(
            "quant_estimate_locked",
            str(exc),
            risk_id=exc.risk_id,
            scenario=exc.scenario,
        ),
    )



async def _not_assemblable(
    request: Request, exc: SimulationNotAssemblable
) -> JSONResponse:
    # 422 for the same reason the quant routes use it: every field parsed and every type
    # is right. What failed is the relationship between the register, the estimates and
    # the schedule, and each issue names something to go and fix.
    return JSONResponse(
        status_code=422,
        content=_payload("simulation_not_assemblable", str(exc), issues=exc.issues),
    )


async def _run_not_cancellable(
    request: Request, exc: SimulationRunNotCancellable
) -> JSONResponse:
    # 409, like the quant lock: the run exists and cancelling is a real verb, just not
    # one this status accepts.
    return JSONResponse(
        status_code=409,
        content=_payload(
            "simulation_run_not_cancellable",
            str(exc),
            run_id=exc.run_id,
            status=exc.status,
        ),
    )


async def _gate_blocked(request: Request, exc: ScheduleGateBlocked) -> JSONResponse:
    # 409, like the delete confirmation: the request is legitimate and the caller may make
    # it. What is missing is a decision — fix the schedule, or own the override.
    return JSONResponse(
        status_code=409,
        content=_payload(
            "schedule_gate_blocked",
            str(exc),
            version_id=exc.version_id,
            blocking_failures=exc.blocking,
        ),
    )


async def _scope_not_found(request: Request, exc: ScopeNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content=_payload("scope_not_found", str(exc), scope_id=exc.scope_id)
    )


async def _scope_invalid(request: Request, exc: ScopeInvalid) -> JSONResponse:
    # 422: the request is well formed, the placement or move it describes is not one the
    # hierarchy can hold.
    return JSONResponse(status_code=422, content=_payload("scope_invalid", str(exc)))


async def _scope_delete_blocked(request: Request, exc: ScopeDeleteBlocked) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_payload(
            "scope_delete_blocked", str(exc), scope_id=exc.scope_id, reasons=exc.reasons
        ),
    )


async def _domain_error(request: Request, exc: RiskPlatformError) -> JSONResponse:
    return JSONResponse(status_code=400, content=_payload("domain_error", str(exc)))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AmbiguousProjectError, _ambiguous_project)  # type: ignore[arg-type]
    app.add_exception_handler(ProjectNotFound, _project_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(ScheduleDeleteBlocked, _delete_blocked)  # type: ignore[arg-type]
    app.add_exception_handler(UnsupportedScheduleFormat, _unsupported_format)  # type: ignore[arg-type]
    app.add_exception_handler(ParserUnavailable, _parser_unavailable)  # type: ignore[arg-type]
    app.add_exception_handler(MalformedScheduleFile, _malformed)  # type: ignore[arg-type]
    app.add_exception_handler(QuantEstimateInvalid, _quant_invalid)  # type: ignore[arg-type]
    app.add_exception_handler(QuantEstimateLocked, _quant_locked)  # type: ignore[arg-type]
    app.add_exception_handler(SimulationNotAssemblable, _not_assemblable)  # type: ignore[arg-type]
    app.add_exception_handler(SimulationRunNotCancellable, _run_not_cancellable)  # type: ignore[arg-type]
    app.add_exception_handler(ScheduleGateBlocked, _gate_blocked)  # type: ignore[arg-type]
    app.add_exception_handler(ScopeNotFound, _scope_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(ScopeInvalid, _scope_invalid)  # type: ignore[arg-type]
    app.add_exception_handler(ScopeDeleteBlocked, _scope_delete_blocked)  # type: ignore[arg-type]
    app.add_exception_handler(RiskPlatformError, _domain_error)  # type: ignore[arg-type]
