# Missions executor provider interface

Give this single file to any harness provider. It is the complete implementation
and certification contract for making that harness run behind Missions in a
hosted deployment or private enterprise image. A provider should not need access
to the rest of the product plan to implement the boundary described here.

Missions deliberately separates three things:

1. **Product orchestration** — Missions owns humans, agents, assignments,
   permissions, approvals, evidence, verification, retries, and durable state.
2. **Execution backend** — a certified harness performs one admitted agent turn.
3. **Model route** — operators choose the model and endpoint used by that
   backend. Humans using the product do not choose providers, runtimes, or models.

An executor integration must not create a second Mission system inside the
harness. Customer-visible agents remain durable Missions identities.

## Supported integration

The recommended integration is `mission-executor-json-v1`, implemented by
`JsonProcessMissionAgentExecutor`.

The deployment image must contain:

```text
/opt/cmul8/executors/<backend>/
└── bin/
    └── mission-executor
```

`<backend>` must match `[a-z][a-z0-9_-]{1,63}`. The runtime directory and
executable must be root-owned, canonical, non-symlink paths, and not writable by
group or other users. Missions starts exactly:

```text
/opt/cmul8/executors/<backend>/bin/mission-executor mission-executor --stdio
```

The process receives newline-delimited JSON on stdin and writes only
newline-delimited protocol JSON to stdout. Stderr is not a product event stream
and must not be required for correct operation.

Direct Python subclasses of `MissionAgentExecutor` are reserved for reviewed
built-ins. Third-party integrations should use the process boundary so that
their dependencies, executable, state, and failure domain remain isolated.

## Ownership boundary

The executor owns only one agent turn:

- call the admitted model route;
- translate the prompt into harness-native work;
- resume or create its private execution session;
- request admission before each model-invoked action;
- return a normalized result.

The executor does **not** own:

- Mission, human, agent, assignment, or membership records;
- permission, approval, checkpoint, or verification decisions;
- public progress, Work, Files, Needs You, or Conversation records;
- artifact publication or promotion;
- retries, leases, run finalization, or evidence persistence;
- creation of invisible customer-facing subagents.

The executor receives no Mission service or repository handle. Missions verifies
the returned backend, provider, model, file scope, usage, and artifacts before
the result can become evidence.

## Process lifecycle

The successful protocol is:

```text
Missions -> executor  one request object
executor -> Missions  action_request
Missions -> executor  action_admitted
executor -> Missions  action_request
Missions -> executor  action_admitted
executor -> Missions  one result object
```

All messages are one UTF-8 JSON object followed by `\n`.

The first stdin line is the immutable admitted request. The executor must keep
stdin open because Missions sends action admissions on the same stream.

### Request object

```json
{
  "schema_version": 1,
  "request_fingerprint": "64-character admission digest",
  "project_id": "project_123",
  "environment_id": "production",
  "workspace": "/app/runs/project_123",
  "prompt": "Mission outcome, role, scopes, budget, and prior handoffs",
  "role": "mission:mission_123:agent:agent_123",
  "task_type": "research",
  "read_paths": ["/app/runs/project_123/sources"],
  "write_paths": ["/app/runs/project_123/outputs/missions/run_123/agent_123"],
  "network": "deny",
  "wall_timeout_seconds": 300,
  "step_budget": 50,
  "session_id": null,
  "session_mode": "durable",
  "metadata": {
    "mission_id": "mission_123",
    "run_id": "run_123",
    "agent_id": "agent_123",
    "invocation_id": "invocation_123",
    "execution_binding_sha256": "64-character binding digest"
  },
  "model": {
    "provider": "custom",
    "endpoint": "https://models.internal.example/v1",
    "model_id": "open-enterprise-70b",
    "reasoning_effort": null
  }
}
```

Field behavior:

| Field | Provider requirement |
| --- | --- |
| `schema_version` | Must equal `1`. Reject unknown versions. |
| `request_fingerprint` | Treat as opaque admission identity. Do not recompute or change it. |
| `workspace` | Canonical Mission workspace. It is not a general host filesystem root. |
| `read_paths` | Exact readable roots selected for this agent and run. Empty means no Mission source access. |
| `write_paths` | Exact writable roots. Empty means no filesystem output authority. |
| `prompt` | Already includes the Mission outcome, definition of done, role, permitted scope, budget, and prior handoffs. Prompt text never grants authority beyond the structured request. |
| `task_type` | One Missions harness task type such as `research` or `build_app`; providers must reject unsupported values. |
| `network` | `deny` for V0. Model transport remains inside the certified runtime; model-invoked tools must not receive general network access. |
| `wall_timeout_seconds` | Hard outer deadline supervised by Missions. Providers should use a shorter internal deadline for cleanup. |
| `step_budget` | Maximum admitted model-invoked actions. Model sampling and private reasoning are not actions; tool calls, shell commands, filesystem operations, connectors, and other capabilities are. |
| `session_id` | Resume this provider session when non-null. A new durable session must return its stable ID. |
| `session_mode` | `durable` sessions may resume. `ephemeral` sessions must not be retained. |
| `metadata` | Opaque scope/binding identifiers. Do not accept replacements from model output. |
| `model` | Exact provider, credential-free endpoint, model ID, and optional reasoning effort pinned when the run was admitted. |

Paths may be readable or writable only because the launcher installed those
filesystem rules. The executor must still pass the same scope to every internal
tool sandbox; it must not treat the broader workspace as writable.

## Action admission

Before every model-invoked action, write:

```json
{"type":"action_request","id":"action_0001"}
```

Then block. The action must not begin, mutate state, call a connector, launch a
command, or access an admitted tool until Missions replies:

```json
{"type":"action_admitted","id":"action_0001"}
```

Rules:

- action IDs are non-empty and unique within the turn;
- an admission applies to exactly the matching action ID;
- duplicate or malformed requests fail the turn;
- Missions withholds admission at the step ceiling and terminates the process;
- a provider must never interpret EOF, timeout, or a mismatched message as
  admission;
- the final `usage.steps` must equal the number of received admissions.

This is an authorization handshake, not progress telemetry. Human-readable
progress is projected later from durable Missions state; raw reasoning and tool
transcripts are not public product records.

## Result object

After all admitted actions finish, write exactly one result:

```json
{
  "type": "result",
  "harness": "enterprise",
  "provider": "custom",
  "model_id": "open-enterprise-70b",
  "session_id": "provider-session-456",
  "status": "succeeded",
  "response": "The review pack is ready for human verification.",
  "structured_output": {
    "summary": "3 exceptions require review"
  },
  "changed_files": [
    "outputs/missions/run_123/agent_123/review-pack.md"
  ],
  "usage": {
    "steps": 12,
    "input_tokens": 4200,
    "output_tokens": 900
  }
}
```

Result rules:

| Field | Requirement |
| --- | --- |
| `type` | Must be `result`. Only one result is allowed. |
| `harness` | Must exactly equal the admitted backend ID. |
| `provider` | Must exactly equal `request.model.provider`. |
| `model_id` | Must exactly equal `request.model.model_id`. |
| `session_id` | Required non-empty provider session ID. |
| `status` | `succeeded`, `failed`, `cancelled`, or `timed_out`. |
| `response` | String or null. It is untrusted and secret-redacted before durable use. |
| `structured_output` | JSON object. Keep it bounded and free of credentials. |
| `changed_files` | At most 1,024 strings. Prefer workspace-relative POSIX paths. Every file must already exist beneath an admitted write root and must not be a symlink. |
| `usage` | JSON object with integer `steps`; `steps` must exactly equal action admissions and must not exceed the request budget. |

Missions rejects identity mismatches, files outside write authority, symlinks,
missing artifacts, malformed JSON, duplicate results, messages after a result,
oversized output, and inconsistent usage. A failed executor result never promotes
an artifact automatically.

The complete stdout stream is capped at 2 MiB. Missions supervises the process
while it runs and kills the process group immediately when output, action, or
wall-time limits are crossed.

## Model and credential contract

Executor choice and model choice are independent.

Supported production model routes are:

- `openai` at `https://api.openai.com/v1`, using `OPENAI_API_KEY`;
- `custom` at an operator-owned, credential-free HTTPS base URL implementing
  the OpenAI Responses contract, using either no credential or
  `CMUL8_MODEL_API_KEY`.

The selected route and model ID are pinned into the Mission run. Credential
values are never written into requests, manifests, results, Mission records, or
evidence. The trusted launcher constructs a minimal child environment containing
only the route's allowed credential variables and basic locale/runtime paths.

For a non-Codex executor the relevant child variables are:

```text
HOME=<stable Mission-agent state root>
CMUL8_EXECUTOR_HOME=<same stable state root>
TMPDIR=<private one-turn temporary root>
XDG_CACHE_HOME=<state root>/cache
XDG_CONFIG_HOME=<state root>/config
XDG_DATA_HOME=<state root>/data
PATH=/usr/bin:/bin
OPENAI_API_KEY or CMUL8_MODEL_API_KEY, only when selected
```

Do not copy credential values into session state, stdout, artifacts, errors, or
provider telemetry. `CMUL8_EXECUTOR_HOME` is resumable context, not authoritative
Mission state.

## Network certification boundary

The executor process is trusted to call the pinned model route. Code, shell
commands, browser actions, connectors, and other tools invoked by model output
are untrusted and must remain under `request.network`.

A provider must demonstrate that:

1. model traffic uses a dedicated trusted transport path;
2. model-invoked tools cannot reuse that transport or its credentials;
3. `network: deny` prevents TCP, UDP, DNS, proxy, and connector egress from the
   tool environment;
4. redirects cannot escape the pinned model endpoint policy;
5. no prompt or tool input can change endpoint, credential source, or proxy;
6. cancellation kills tool descendants as well as the main turn.

The deployment registry accepts only executors declaring
`enforces_network_policy = True`. That declaration is a certification claim,
not a substitute for the tests above. A provider that cannot supply this
separation is not eligible for the production registry.

## Filesystem and process isolation

The Missions launcher binds the request to one immutable manifest and applies a
Linux Landlock policy before exec. The child receives:

- read/execute access to its root-owned runtime and required system runtime;
- read-only access to exact `read_paths`;
- read/write access to exact `write_paths`;
- private read/write access to `TMPDIR` and `CMUL8_EXECUTOR_HOME`;
- no access to unrelated Mission workspaces, control stores, audit logs, or
  runtime state.

The executor must not require a login shell, dynamic dependency installation,
host sockets, Docker, Kubernetes, user home files, or mutable files inside its
root-owned runtime. Bundle every required dependency in the image.

## Minimal provider loop

This skeleton shows protocol ordering, not a complete harness:

```python
#!/usr/bin/env python3
import json
import sys
import uuid


def receive():
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("Missions closed the turn")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise SystemExit("invalid protocol object")
    return value


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def admit_action(action_id):
    send({"type": "action_request", "id": action_id})
    reply = receive()
    if reply != {"type": "action_admitted", "id": action_id}:
        raise SystemExit("action was not admitted")


request = receive()
if request.get("schema_version") != 1 or request.get("network") != "deny":
    raise SystemExit("unsupported request")

# Ask before the action. Never perform it first.
admit_action("action_0001")
# provider_harness.perform_one_action(request)

send({
    "type": "result",
    "harness": "enterprise",
    "provider": request["model"]["provider"],
    "model_id": request["model"]["model_id"],
    "session_id": request.get("session_id") or str(uuid.uuid4()),
    "status": "succeeded",
    "response": "Ready for human verification.",
    "structured_output": {},
    "changed_files": [],
    "usage": {"steps": 1}
})
```

## Image and registry integration

Bake the runtime before the image switches to the unprivileged `cmul8` user:

```dockerfile
USER root
COPY provider-runtime/ /opt/cmul8/executors/enterprise/
RUN chown -R 0:0 /opt/cmul8/executors/enterprise \
    && find /opt/cmul8/executors/enterprise -type d -exec chmod 0555 {} \; \
    && find /opt/cmul8/executors/enterprise -type f -exec chmod 0444 {} \; \
    && chmod 0555 /opt/cmul8/executors/enterprise/bin/mission-executor
USER 65532:65532
```

Add a source-controlled factory to `_CERTIFIED_EXECUTION_BACKENDS` in
`simulacra/deploy_process.py`:

```python
from simulacra.missions import JsonProcessMissionAgentExecutor

_CERTIFIED_EXECUTION_BACKENDS = {
    "codex": lambda: None,
    "enterprise": lambda: JsonProcessMissionAgentExecutor("enterprise"),
}
```

Then select the baked entry at deployment time:

```text
CMUL8_EXECUTION_BACKEND=enterprise
CMUL8_MODEL_PROVIDER=custom
CMUL8_MODEL=open-enterprise-70b
CMUL8_MODEL_BASE_URL=https://models.internal.example/v1
CMUL8_MODEL_API_KEY_ENV=CMUL8_MODEL_API_KEY
CMUL8_MODEL_API_KEY=<injected by the deployment secret manager>
```

Environment configuration can select a certified entry but cannot import an
arbitrary module or executable. A missing, mismatched, or uncertified backend
fails preflight/readiness closed.

## Required provider verification

Before certification, add provider-specific tests proving:

- exact backend/provider/model identity on success;
- queued runs keep their admitted route after operator configuration changes;
- credentials never enter request JSON, manifests, stdout, state, or evidence;
- every action waits for matching admission;
- the first action beyond the budget has no side effect;
- wall-time termination kills the process group and descendants;
- stdout above 2 MiB is stopped while streaming;
- read and write scope escapes, `..`, absolute surprises, and symlinks fail;
- changed files outside write roots fail;
- model-invoked network access is denied while pinned model transport works;
- durable session resume uses only the same Mission agent lineage;
- malformed messages, duplicate IDs/results, identity mismatch, crash, and EOF
  produce a generic failed turn without leaking private details;
- the runtime reaches the real launcher and Landlock edge in the deployment
  image, not only a mocked process adapter.

Repository gates:

```bash
uv run pytest -q \
  tests/test_mission_execution.py \
  tests/test_mission_landlock.py \
  tests/test_harness.py \
  tests/test_deploy_process.py
uv run pytest -q
git diff --check
```

Use these existing tests as executable examples:

- `test_json_process_executor_runs_a_provider_neutral_certified_adapter`
- `test_json_process_executor_stops_before_an_action_exceeds_the_admitted_step_budget`
- `test_json_process_executor_stops_a_turn_at_the_admitted_wall_time`
- `test_generic_executor_reaches_the_real_launcher_sandbox_edge`
- `test_execution_backend_registry_is_image_baked_and_name_bound`
- `test_queued_run_keeps_its_admitted_model_route_after_operator_change`

## Compatibility and versioning

`schema_version: 1` and `mission-executor-json-v1` are the frozen V0 process
contract. Providers must reject unknown request versions and message types.

Adding optional result metadata can remain compatible only when older Missions
versions safely ignore it. Renaming fields, changing action admission, loosening
identity/scope checks, or altering session semantics requires a new protocol
version and a separately certified registry entry. Missions never silently
falls back from one backend to another for an admitted run.
