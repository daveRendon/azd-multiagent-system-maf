"""Utility script to verify the triage workflow responds to a ticket."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Ensure repository root is importable when running as a file path.
    sys.path.append(str(Path(__file__).resolve().parents[1]))
from typing import Optional

from src.api.triage_workflow import (
    MissingEnvironmentError,
    TriageWorkflow,
    WorkflowExecutionError,
    WorkflowNotReadyError,
    WorkflowResultError,
)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    print(f"Loading environment values from {path}")
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_quotes(value)
            existing = os.environ.get(key)
            if existing and existing != value:
                print(f"Overriding environment variable {key} (was '{existing}', now '{value}')")
            os.environ[key] = value


def _detect_azd_env_name() -> Optional[str]:
    explicit = os.getenv("AZURE_ENV_NAME")
    if explicit:
        return explicit

    config_path = Path(".azure") / "config.json"
    if not config_path.exists():
        return None
    try:
        import json as _json

        with config_path.open("r", encoding="utf-8") as handle:
            data = _json.load(handle)
    except (OSError, ValueError):
        return None
    defaults = data.get("defaults", {})
    candidate = defaults.get("environment") or data.get("defaultEnvironment")
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _initialize_env(explicit_path: Optional[str]) -> None:
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    azure_env = _detect_azd_env_name()
    if azure_env:
        candidates.append(Path(".azure") / azure_env / ".env")
    candidates.append(Path(".env"))

    for candidate in candidates:
        try:
            _load_env_file(candidate)
        except OSError as exc:
            print(f"Warning: failed to read {candidate}: {exc}")

    if "AZURE_AI_PROJECT_ENDPOINT" not in os.environ:
        legacy = os.environ.get("AIFOUNDRY_PROJECT_ENDPOINT") or os.environ.get("projectEndpoint")
        if legacy:
            print("Setting AZURE_AI_PROJECT_ENDPOINT from legacy value")
            os.environ["AZURE_AI_PROJECT_ENDPOINT"] = legacy

    if "AZURE_AI_MODEL_DEPLOYMENT_NAME" not in os.environ:
        legacy_model = os.environ.get("TRIAGE_MODEL_DEPLOYMENT_NAME") or os.environ.get(
            "AIFOUNDRY_AGENT_MODEL"
        )
        if legacy_model:
            print("Setting AZURE_AI_MODEL_DEPLOYMENT_NAME from legacy value")
            os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"] = legacy_model


async def _run_verification(ticket: str, show_trace: bool) -> int:
    workflow = TriageWorkflow()
    try:
        await workflow.startup()
    except MissingEnvironmentError as exc:
        print(f"Missing environment configuration: {exc}", file=sys.stderr)
        return 1

    try:
        if show_trace:
            result, trace = await workflow.triage_with_trace(ticket)
        else:
            result = await workflow.triage(ticket)
            trace = None
    except (WorkflowNotReadyError, WorkflowExecutionError, WorkflowResultError) as exc:
        print(f"Workflow execution failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await workflow.shutdown()

    print(json.dumps(result, indent=2))

    if trace is not None:
        print("\n--- Workflow trace ---")
        for executor_id, messages in trace.messages.items():
            print(f"\n[{executor_id}]")
            for message in messages:
                print(message)

    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the triage workflow against a sample ticket.")
    parser.add_argument(
        "--ticket",
        default="VPN outage affecting finance team",
        help="Ticket text to evaluate.",
    )
    parser.add_argument(
        "--env-file",
        help="Optional path to an env file to load before verification.",
    )
    parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Print incremental outputs recorded during the workflow run.",
    )

    args = parser.parse_args(argv)

    _initialize_env(args.env_file)

    return asyncio.run(_run_verification(ticket=args.ticket.strip(), show_trace=args.show_trace))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
