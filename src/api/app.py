from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, HTTPException

from .triage_workflow import (
    MissingEnvironmentError,
    TriageWorkflow,
    WorkflowExecutionError,
    WorkflowNotReadyError,
    WorkflowResultError,
)


app = FastAPI()
workflow = TriageWorkflow()


@app.on_event("startup")
async def _startup() -> None:
    try:
        await workflow.startup()
    except MissingEnvironmentError as exc:
        raise RuntimeError(str(exc)) from exc


@app.on_event("shutdown")
async def _shutdown() -> None:
    await workflow.shutdown()


@app.get("/")
async def health() -> dict[str, Any]:
    return {"status": "ok", **workflow.environment_snapshot()}


@app.post("/triage")
async def triage(ticket: str = Body(..., embed=True)) -> dict[str, Any]:
    text = ticket.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Ticket content cannot be empty.")

    try:
        return await workflow.triage(text)
    except WorkflowNotReadyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (WorkflowExecutionError, WorkflowResultError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Workflow execution failed: {exc}") from exc
