from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.api.triage_workflow import (
    MissingEnvironmentError,
    TriageWorkflow,
    WorkflowExecutionError,
    WorkflowNotReadyError,
    WorkflowResultError,
)


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, inner in value.items():
            new_key = " ".join(str(key).split()).strip() if isinstance(key, str) else key
            sanitized[new_key] = _sanitize(inner)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        compact = " ".join(value.split())
        return compact.strip()
    return value

try:
    # Handle both `python scripts/...` and `python -m scripts...` invocation styles.
    from scripts.verify_agent import _initialize_env  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback when executed as a script
    from verify_agent import _initialize_env


async def _execute(ticket: str) -> tuple[dict, list[tuple[str, list[str]]]]:
    workflow = TriageWorkflow()
    try:
        await workflow.startup()
    except MissingEnvironmentError as exc:
        raise RuntimeError(f"Missing environment configuration: {exc}") from exc

    try:
        result, trace = await workflow.triage_with_trace(ticket)
    except (WorkflowNotReadyError, WorkflowExecutionError, WorkflowResultError) as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        await workflow.shutdown()

    return result, list(trace.messages.items())


def _print_trace(trace_items: Iterable[tuple[str, list[str]]]) -> None:
    for executor_id, messages in trace_items:
        print(f"\n[{executor_id}]")
        if executor_id == "priority-analyst":
            print(messages)
        combined = " ".join(part.strip() for part in messages if part.strip())
        if not combined:
            continue

        try:
            parsed = json.loads(combined)
        except json.JSONDecodeError:
            normalized = (
                combined.replace('" \n', '" ').replace('\n "', ' "').replace('\n', ' ')
            )
            normalized = re.sub(r"\s+", " ", normalized).strip()
            for symbol in [",", ":", "{", "}", "[", "]"]:
                normalized = normalized.replace(f" {symbol}", symbol).replace(f"{symbol} ", symbol)
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                print(normalized)
            else:
                print(json.dumps(_sanitize(parsed), indent=2))
        else:
            print(json.dumps(_sanitize(parsed), indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the sequential triage workflow and inspect each step.")
    parser.add_argument(
        "--ticket",
        default="VPN outage affecting finance team",
        help="Ticket text to evaluate.",
    )
    parser.add_argument(
        "--env-file",
        help="Optional path to an env file to load before invoking the workflow.",
    )

    args = parser.parse_args()

    _initialize_env(args.env_file)

    try:
        result, trace_items = asyncio.run(_execute(args.ticket.strip()))
    except RuntimeError as exc:
        print(f"Workflow execution failed: {exc}", file=sys.stderr)
        return 1

    print("=== Aggregated triage result ===")
    print(json.dumps(_sanitize(result), indent=2))

    print("\n=== Participant outputs ===")
    _print_trace(trace_items)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
