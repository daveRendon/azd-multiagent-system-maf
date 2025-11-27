from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from agent_framework import SequentialBuilder, WorkflowOutputEvent
from agent_framework.azure import AzureAIAgentClient
from azure.identity.aio import DefaultAzureCredential


class MissingEnvironmentError(RuntimeError):
    """Raised when required Azure AI environment variables are absent."""


class WorkflowNotReadyError(RuntimeError):
    """Raised when the workflow has not been initialized."""


class WorkflowExecutionError(RuntimeError):
    """Raised when workflow execution fails."""


class WorkflowResultError(RuntimeError):
    """Raised when the workflow finishes without producing a result."""


@dataclass(frozen=True)
class TriageTrace:
    messages: dict[str, list[str]]


class TriageWorkflow:
    """Manages the triage workflow lifecycle for reuse across processes."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._client: AzureAIAgentClient | None = None
        self._workflow = None
        self._env_info: dict[str, str | None] | None = None

    async def startup(self) -> None:
        if self._workflow is not None:
            return

        project_endpoint = self._resolve_project_endpoint()
        model_deployment = self._resolve_model_deployment()
        self._env_info = {
            "project_endpoint": project_endpoint,
            "model_deployment_name": model_deployment,
        }

        self._stack = AsyncExitStack()
        credential = await self._stack.enter_async_context(DefaultAzureCredential())
        self._client = await self._stack.enter_async_context(
            AzureAIAgentClient(async_credential=credential)
        )

        participants = []
        for spec in _PARTICIPANT_SPECS:
            agent = await self._stack.enter_async_context(
                self._client.create_agent(name=spec["name"], instructions=spec["instructions"])
            )
            participants.append(agent)

        self._workflow = SequentialBuilder().participants(participants).build()

    async def shutdown(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._client = None
        self._workflow = None
        self._env_info = None

    async def triage(self, ticket: str) -> dict[str, Any]:
        result, _ = await self._run(ticket, capture_trace=False)
        return result

    async def triage_with_trace(self, ticket: str) -> tuple[dict[str, Any], TriageTrace]:
        result, trace = await self._run(ticket, capture_trace=True)
        return result, TriageTrace(messages=trace)

    def environment_snapshot(self) -> dict[str, str | None]:
        if self._env_info is not None:
            return dict(self._env_info)
        return {
            "project_endpoint": os.getenv("AZURE_AI_PROJECT_ENDPOINT")
            or os.getenv("AIFOUNDRY_PROJECT_ENDPOINT"),
            "model_deployment_name": os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            or os.getenv("TRIAGE_MODEL_DEPLOYMENT_NAME"),
        }

    async def _run(self, ticket: str, *, capture_trace: bool) -> tuple[dict[str, Any], dict[str, list[str]]]:
        if self._workflow is None:
            raise WorkflowNotReadyError("Call startup() before triage().")

        trace: dict[str, list[str]] = {} if capture_trace else {}
        result: dict[str, Any] | None = None

        try:
            async for event in self._workflow.run_stream(ticket):
                executor_id = getattr(event, "executor_id", None)
                if capture_trace and executor_id:
                    text = self._stringify_event_data(event)
                    if text:
                        trace.setdefault(executor_id, []).append(text)
                if isinstance(event, WorkflowOutputEvent):
                    if capture_trace:
                        text = self._stringify_event_data(event)
                        if text:
                            trace.setdefault(executor_id or "workflow", []).append(text)
                    result = self._extract_json(event.data)
        except Exception as exc:  # pragma: no cover - propagated to caller
            raise WorkflowExecutionError(str(exc)) from exc

        if result is None:
            raise WorkflowResultError("Workflow completed without emitting a result.")
        return result, trace

    @staticmethod
    def _resolve_project_endpoint() -> str:
        endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        if endpoint:
            return endpoint
        legacy = os.getenv("AIFOUNDRY_PROJECT_ENDPOINT")
        if legacy:
            os.environ.setdefault("AZURE_AI_PROJECT_ENDPOINT", legacy)
            return legacy
        raise MissingEnvironmentError(
            "Set AZURE_AI_PROJECT_ENDPOINT or AIFOUNDRY_PROJECT_ENDPOINT before starting the workflow."
        )

    @staticmethod
    def _resolve_model_deployment() -> str:
        deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        if deployment:
            return deployment
        legacy = os.getenv("TRIAGE_MODEL_DEPLOYMENT_NAME")
        if legacy:
            os.environ.setdefault("AZURE_AI_MODEL_DEPLOYMENT_NAME", legacy)
            return legacy
        raise MissingEnvironmentError(
            "Set AZURE_AI_MODEL_DEPLOYMENT_NAME or TRIAGE_MODEL_DEPLOYMENT_NAME before starting the workflow."
        )

    @staticmethod
    def _extract_json(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            samples: list[str] = []
            for item in payload:
                try:
                    return TriageWorkflow._extract_json(item)
                except ValueError:
                    content = getattr(item, "content", None)
                    text_attr = getattr(item, "text", None)
                    samples.append(
                        f"content_type={type(content)!r} content={repr(content)[:500]} text={repr(text_attr)[:500]} attrs={dir(item)}"
                    )
                    if not content and text_attr is None:
                        continue
                    candidates = []
                    if content:
                        if isinstance(content, str):
                            candidates.append(content)
                        else:
                            for part in content:
                                text = getattr(part, "text", None)
                                if text is None:
                                    continue
                                value = getattr(text, "value", text)
                                if value:
                                    candidates.append(str(value))
                    if text_attr is not None:
                        if isinstance(text_attr, str):
                            candidates.append(text_attr)
                        else:
                            value = getattr(text_attr, "value", text_attr)
                            if value:
                                candidates.append(str(value))
                    for candidate in candidates:
                        samples.append(str(candidate)[:500])
                        try:
                            return TriageWorkflow._extract_json(candidate)
                        except ValueError:
                            continue
            hint = f" candidates={samples!r}" if samples else ""
            raise ValueError(
                f"No JSON object found in workflow output list: {payload!r}{hint}"
            )
        if not isinstance(payload, str):
            raise ValueError(f"Unexpected payload type: {type(payload)!r}")
        cleaned = payload.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match is None:
                raise ValueError("No JSON object found in workflow output")
            return json.loads(match.group(0))

    @staticmethod
    def _stringify_event_data(event: Any) -> str | None:
        data = getattr(event, "data", None)
        if data is None:
            return None
        if isinstance(data, str):
            normalized = data.replace("\r\n", "\n")
            normalized = normalized.replace('"\n', '"').replace('\n"', '"').replace("\n", " ")
            normalized = re.sub(r"\s+", " ", normalized).strip()
            try:
                parsed = TriageWorkflow._extract_json(normalized)
            except ValueError:
                return normalized
            return json.dumps(parsed)
        if isinstance(data, (dict, list)):
            try:
                return json.dumps(data)
            except TypeError:
                return str(data)
        return str(data)


_PARTICIPANT_SPECS = [
    {
        "name": "priority-analyst",
        "instructions": (
            "You are a support triage specialist. Analyze the user ticket and respond "
            "with JSON containing `priority` (Critical, High, Medium, or Low) and `notes` "
            "explaining the decision. Do not add extra text outside the JSON."
        ),
    },
    {
        "name": "team-router",
        "instructions": (
            "You assign tickets to teams. Based on the conversation so far, respond "
            "with JSON containing `team` (choose one of Platform, Integrations, Data, or "
            "Support) and `notes` describing the reasoning. Do not add text outside the JSON payload."
        ),
    },
    {
        "name": "effort-estimator",
        "instructions": (
            "Estimate the level of effort for the ticket. Reply with JSON containing `effort` "
            "(S, M, or L where S is < 2 hours) and `notes` explaining your answer. Respond with JSON only."
        ),
    },
    {
        "name": "triage-aggregator",
        "instructions": (
            "Summarize the prior agent outputs into a single JSON object with keys `priority`, `team`, "
            "`effort`, and `summary`. Use the previously provided JSON snippets to populate the fields. "
            "Keep values concise and respond with JSON only."
        ),
    },
]
