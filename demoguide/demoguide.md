# Multi-Agent Demo Walkthrough

This playbook contains the exact sequence to stand up, warm up, and present the Azure AI multi-agent triage solution during a live demo. Follow the numbered steps in order; each section calls out the commands to run, the story to tell, and the screenshot to show (when available).

---

## 0. Prerequisites

- Azure subscription with access to GPT-4o via Azure AI Foundry
- Azure Developer CLI (`azd`) and Azure CLI (`az`) logged in (`az login`)
- Python 3.12+ with `pip`
- Optional: Docker if you plan to rebuild the API container

From the repo root, install Python dependencies (once per machine):

```pwsh
pip install --pre -r src/api/requirements.txt
```

> If you already ran the quick start before the demo, ensure you are using the same `azd` environment that holds the outputs you plan to showcase.

---

## 1. Initialize the Environment

```pwsh
azd env new demo              # or `azd env select demo` if it already exists
azd auth login                # optional; guarantees you are using the right tenant
```

- Mention that the repo ships with `azure.yaml`, so `azd` knows where the infrastructure and app code live.
- Point out that `azd env list` shows all available environments if you need to double-check.

---

## 2. Provision Infrastructure (`azd up`)

```pwsh
azd up
```

Narration points:

- `azd up` orchestrates the Bicep modules under `infra/` to create everything end-to-end: Azure Container Apps environment, container app, container registry, Azure AI Foundry project, GPT-4o deployment, managed identities, and diagnostics resources.
- Highlight the `infra/modules/foundry.bicep` module when explaining how the GPT-4o deployment is created automatically with capacity 2 (modifiable via `modelSkuCapacity`).
- Call out that the command publishes the FastAPI container with environment variables injected from the deployment outputs.
- Show `azd-multiagent-resource-group.png` to display the provisioned resource group and the resources inside it.

When the command finishes, demo the generated outputs:

```pwsh
azd env get-values
```

- Point at `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, and `APIURL` (container app URL). These will feed the scripts in the next steps.

---

## 3. Warm Up the Workflow (`bootstrap_agents.py`)

```pwsh
python scripts/bootstrap_agents.py --ticket "VPN outage affecting finance team" --output warmup.json
```

Narration points:

- The script uses `src/api/triage_workflow.py` to instantiate the four agents (priority, team, effort, triage) in-process using the Microsoft Agent Framework.
- The tool waits for DNS propagation, resolves the Azure AI project endpoint/model deployment, and prints an environment snapshot so you can reassure the audience that everything is wired correctly.
- Optional `--ticket` performs a full warm-up run and returns JSON for the aggregated triage result; `--output` saves the payload for later reference.
- If you omit `--ticket`, the script exits after initialization—useful for quick health checks between takes.
- The console now prints the aggregated result plus each participant's contribution as compact, easy-to-read JSON; highlight how this makes it simple to narrate the workflow without post-processing the output.
- If you notice stray internal spaces inside certain values (for example `"eff ort"`), call out that the script preserves the model's wording while only normalizing the layout.

After the command finishes, open `warmup.json` in VS Code to show the structured JSON. Use `azd-multiagent-agents-in-ai-foundry.png` to reinforce that the four agents are conceptually represented.

---

## 4. Exercise the Workflow End-to-End (`test_all_agents.py`)

```pwsh
python scripts/test_all_agents.py --ticket "Teams chat is down for the engineering org"
```

Narration points:

- `test_all_agents.py` automatically loads `.azure/<env>/.env`, mapping legacy names (`projectEndpoint`) to the new variables if necessary, so you do not need to export anything manually.
- It sequentially runs each participant plus the triage aggregator and prints their responses with labels, making it the fastest way to confirm the full workflow during a demo.
- All participant responses are rendered as normalized JSON blocks, so you can read priority/team/effort summaries directly from the terminal without wading through line-wrapped tokens.
- Occasional internal spacing quirks originate from the model output itself; the script keeps the text semantically intact while trimming the surrounding whitespace.
- Mention that the script overwrites any stale environment variables detected in the current shell to avoid "agent not found" issues after redeployments.
- Show `azd-multiagent-agents-responses.png` to visualize the console output structure.

---

## 5. Inspect Individual Agents (`verify_agent.py`)

```pwsh
python scripts/verify_agent.py `
	--agent-id $Env:TRIAGE_AGENT_ID `
	--ticket "VPN outage affecting finance team" `
	--max-attempts 12 `
	--initial-backoff 12 `
	--max-backoff 60 `
	--show-transcript
```

Narration points:

- Use this command if you want to spotlight retry/backoff behavior or show the transcript for a single agent. Swap `TRIAGE_AGENT_ID` for `PRIORITY_AGENT_ID`, `TEAM_AGENT_ID`, or `EFFORT_AGENT_ID` when troubleshooting.
- The script logs run status transitions (`queued → running → completed`) and writes out the exchange if you pass `--show-transcript`.
- Show `azd-multiagent-agents-running.png` to illustrate how the status output looks during a live run.

---

## 6. Show the API Surface

The deployed container app exposes two endpoints:

- `GET /` – health probe
- `POST /triage` – accepts `{ "ticket": "..." }` and returns the aggregated JSON

Demonstrate the API using the `apiUrl` output:

```pwsh
$base = (azd env get-value apiUrl)
Invoke-RestMethod "$base/" -Method Get
Invoke-RestMethod "$base/triage" -Method Post -Body (@{ ticket = "VPN outage affecting finance" } | ConvertTo-Json) -ContentType 'application/json'
```

- Mention that the FastAPI app uses the same `TriageWorkflow` class under the hood, so the responses mirror what you saw in the scripts.
- Show `azd-multi-agent-output.png` if you want a slide-friendly depiction of the API response.

---

## 7. Portal Deep Dive (Optional)

If you have extra time, walk through these portal blades:

1. **Azure AI Foundry project** – show the model deployment (`azd-multiagent-model-deployment.png`) and describe capacity management and throttling.
2. **Container App logs** – demonstrate how Log Analytics captures invocations and errors.
3. **Managed identity assignments** – reinforce secure, passwordless access from the container app to the AI project.

Keep each segment short—this is optional color for technical audiences.

---

## 8. Troubleshooting Checklist

Work through the items in order if something breaks during a run:

1. **Environment sanity** – `azd env get-values` and ensure the AI endpoint and model deployment variables are present.
2. **Credential refresh** – run `azd auth login` or `az login` to refresh tokens before re-running scripts.
3. **DNS propagation** – rerun `python scripts/bootstrap_agents.py` (without `--ticket`) and watch for the DNS wait loop message if the AI endpoint has not propagated yet.
4. **Agent recreation** – if `verify_agent.py` returns "No assistant found", rerun the bootstrap script to rebuild the workflow.
5. **Rate limits** – increase `--max-attempts`/`--max-backoff` on the verification script or bump `modelSkuCapacity` in Bicep, then redeploy.
6. **Container diagnostics** – use Azure Monitor Logs to query `ContainerAppConsoleLogs_CL` for errors around the time of your run.

Document any surprises during rehearsal so you can pre-empt them while presenting.

---

## 9. Cleanup

```pwsh
azd down
```

- Emphasize that this deletes the resource group created in Step 2, removing the GPT-4o deployment and container app to stop charges.
- Alternatively, delete the resource group manually from the portal if you want to leave the `azd` environment metadata intact.

---

By following this sequence you can narrate the full journey—from infrastructure provisioning to agent orchestration and API consumption—without needing ad-hoc setup steps during the demo.
