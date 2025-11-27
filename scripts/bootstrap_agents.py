"""Warm up the triage workflow and verify configuration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
import re

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.api.triage_workflow import (
    MissingEnvironmentError,
    TriageWorkflow,
    WorkflowExecutionError,
    WorkflowNotReadyError,
    WorkflowResultError,
)

try:
    # Allow execution as a script or via `python -m scripts.bootstrap_agents`.
    from scripts.verify_agent import _initialize_env
except ModuleNotFoundError:  # pragma: no cover - fallback when running directly
    from verify_agent import _initialize_env


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


async def _bootstrap(ticket: Optional[str], output: Optional[Path]) -> int:
    workflow = TriageWorkflow()
    try:
        await workflow.startup()
    except MissingEnvironmentError as exc:
        print(f"Missing environment configuration: {exc}", file=sys.stderr)
        return 1

    print("Workflow initialized successfully.")
    print(json.dumps(workflow.environment_snapshot(), indent=2))

    if not ticket:
        await workflow.shutdown()
        return 0

    try:
        result, trace = await workflow.triage_with_trace(ticket)
    except (WorkflowNotReadyError, WorkflowExecutionError, WorkflowResultError) as exc:
        print(f"Workflow execution failed: {exc}", file=sys.stderr)
        await workflow.shutdown()
        return 1

    await workflow.shutdown()

    print("\nSample triage result:")
    print(json.dumps(_sanitize(result), indent=2))

    if output:
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved workflow output to {output}")

    if trace.messages:
        print("\nParticipant trace:")
        for executor_id, messages in trace.messages.items():
            print(f"\n[{executor_id}]")
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

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize and optionally warm up the triage workflow.")
    parser.add_argument(
        "--ticket",
        help="Optional ticket text to execute as a warm-up run.",
    )
    parser.add_argument(
        "--env-file",
        help="Optional path to an env file to load before initialization.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional file path to write the warm-up result as JSON.",
    )

    args = parser.parse_args()

    if args.output and args.output.is_dir():
        print("--output must reference a file, not a directory.", file=sys.stderr)
        return 1

    _initialize_env(args.env_file)

    return asyncio.run(_bootstrap(args.ticket.strip() if args.ticket else None, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
