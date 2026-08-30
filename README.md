# CMUL8 / Simulacra

CMUL8 is a governed, multiplayer builder for operational internal software. A team describes a business process, reviews the generated Operation Graph, builds an application from the exact approved revision, and runs the resulting work with people and bounded runtime agents.

Simulacra is the implementation repository: FastAPI control plane, React console, Codex builder harness, durable workflow runtime, Project Room collaboration, observability, and single-VM deployment.

## What the V0 can do

- Turn a plain-language process into a generic Operation Graph: entities, workflows, tasks, approvals, permissions, agents, connectors, automations, and views.
- Require an owner or admin to approve the exact immutable graph revision before a build can consume it.
- Build a working React application through the official Codex app-server protocol. Codex is the default; Prime remains an explicit compatibility adapter.
- Coordinate multiple users in a durable Project Room with presence, task claims, transitions, comments, reviews, invitations, and room-role permissions.
- Execute approved workflow transitions and connector actions through durable jobs with leases, retries, idempotency, cancellation, and dead letters.
- Record audit and runtime telemetry without persisting raw credentials or connector errors.
- Run on one machine with Docker Compose, PostgreSQL, Redis, persistent data volumes, an API process, and a real worker process.

There is no bundled vendor-onboarding workflow or fixed industry template. A new project starts from the process you describe.

## Product loop

```text
Describe a process
    -> Architect proposes an Operation Graph
    -> Owner/admin reviews and approves an exact revision
    -> Codex builds the application from that revision
    -> Team coordinates work in the Project Room
    -> Runtime worker executes approved jobs and actions
    -> Audit, telemetry, versions, and rollback preserve the trail
```

## Quick start: local development

### Prerequisites

- Python 3.11 or newer
- Node.js 22 or newer
- npm
- Codex CLI 0.148.x, authenticated locally, **or** an `OPENAI_API_KEY`

Confirm Codex is available:

```bash
codex --version
```

The production image pins `@openai/codex@0.148.0` because the app-server protocol is versioned.

### 1. Configure the environment

```bash
cp .env.example .env
```

At minimum, edit `.env` and set:

```dotenv
OPENAI_API_KEY=your-key-if-not-using-local-codex-auth
SIMULACRA_BOOTSTRAP_EMAIL=admin@localhost
SIMULACRA_BOOTSTRAP_PASSWORD=choose-a-long-password
```

Load the variables into the current shell:

```bash
set -a
source .env
set +a
```

### 2. Start the API and console

```bash
chmod +x scripts/demo.sh
./scripts/demo.sh
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) and sign in with the bootstrap account configured above. The API runs at [http://127.0.0.1:8000](http://127.0.0.1:8000).

### 3. Build the first project

1. Create a project and describe a neutral business process, for example: `Track customer requests from intake through review and resolution.`
2. Open the Project Room and inspect the proposed Operation Graph.
3. As the room owner or admin, approve the exact graph revision.
4. Build the application and open its preview.
5. Invite a second user, create or claim a task, transition it to review, and submit a review from a reviewer or approver account.
6. Inspect runtime and audit activity in the Project Room.

Build and iteration are intentionally blocked when the graph is missing, stale, changed, unsafe, or unapproved.

## Recommended V0 deployment: Docker Compose

Kubernetes is not required for V0. The supported private deployment is a single Linux host running Docker Compose.

### 1. Prepare secrets

```bash
cp .env.example .env
```

Set these values in `.env`:

```dotenv
OPENAI_API_KEY=your-openai-key
SIMULACRA_BOOTSTRAP_EMAIL=admin@example.com
SIMULACRA_BOOTSTRAP_PASSWORD=use-a-long-random-password
CMUL8_POSTGRES_PASSWORD=use-another-long-random-password
CMUL8_TENANT_ID=default
CMUL8_ENVIRONMENT=production
CMUL8_BIND_ADDRESS=127.0.0.1
CMUL8_PORT=8000
```

### 2. Boot the stack

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/readyz
```

Compose automatically runs a fail-closed private-deployment preflight before
migrations. Before opening a production installation to humans, record the
latest backup and restore-drill evidence references and run:

```bash
docker compose run --rm api doctor --format human
```

See the [private deployment readiness contract](docs/private-runtime/readiness.md)
for the machine-readable release report and approval workflow.

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The production API serves the built console from the same origin.

Follow logs:

```bash
docker compose logs -f api worker
```

Stop services without deleting persistent volumes:

```bash
docker compose down
```

The stack contains:

| Service | Responsibility |
| --- | --- |
| `postgres` | Identity and relational application state |
| `redis` | Worker wake-up and readiness transport; durable job truth remains in scoped storage |
| `migrate` | Database migration gate before API and worker start |
| `api` | FastAPI control plane, console, auth, Project Room, graph, build, and observability APIs |
| `worker` | Discovers approved project/revision scopes and executes durable runtime jobs |

Named volumes preserve PostgreSQL, Redis, CMUL8 data, runs, approved graphs, runtime state, and telemetry across normal restarts.

### Production edge requirements

- Keep the application bound to loopback and put a TLS reverse proxy or private ingress in front of it.
- Use a digest-pinned `CMUL8_IMAGE` instead of a mutable image tag.
- Back up `cmul8-postgres`, `cmul8-data`, and `cmul8-runs` together and test restore before onboarding a real team.
- Rotate the bootstrap password and any generated API keys after the first owner signs in.
- Never put raw connector secrets in an Operation Graph. Store only opaque references such as `credential_ref` or `secret_ref` and resolve them in the connector boundary.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CMUL8_EXECUTION_BACKEND` | `codex` | Selects an executor already reviewed and baked into the deployment image. It is never exposed as an end-user choice. |
| `CMUL8_AGENT_HARNESS` | unset | Deprecated compatibility alias for `CMUL8_EXECUTION_BACKEND`; a non-empty canonical value wins. |
| `CMUL8_MODEL_PROVIDER` | `openai` | Operator route: `openai` or a Responses-compatible `custom` endpoint. This is not an end-user choice. |
| `CMUL8_MODEL` | account default | Operator-selected model ID for new runs. Each run binds its selected model immutably. |
| `CMUL8_MODEL_BASE_URL` | unset | HTTPS base URL when `CMUL8_MODEL_PROVIDER=custom`. Query strings and embedded credentials are rejected. |
| `CMUL8_MODEL_API_KEY_ENV` | unset | Set only to `CMUL8_MODEL_API_KEY` when the custom endpoint requires authentication. |
| `CMUL8_MODEL_API_KEY` | unset | Credential for the custom model endpoint; never exposed to agent tools or persisted evidence. |
| `CMUL8_MODEL_REASONING_EFFORT` | unset | Optional reasoning-effort override for new runs. |
| `OPENAI_API_KEY` | unset | OpenAI authentication for container and non-interactive environments. |
| `SIMULACRA_AUTH_REQUIRED` | `1` | Enables authenticated multi-user access. |
| `SIMULACRA_BOOTSTRAP_EMAIL` | `admin@localhost` | Initial owner email. |
| `SIMULACRA_BOOTSTRAP_PASSWORD` | local fallback only | Initial owner password; set it explicitly outside throwaway local use. |
| `SIMULACRA_DATABASE_URL` | local JSON identity store | PostgreSQL identity URL; Compose configures it automatically. |
| `SIMULACRA_DATA_DIR` | environment-specific | Durable control-plane data root. |
| `SIMULACRA_RUNS_DIR` | environment-specific | Project workspaces, generated apps, and run artifacts. |
| `CMUL8_RUNTIME_ROOT` | runs directory | Durable runtime job and workflow state. |
| `CMUL8_TELEMETRY_ROOT` | runs directory | Shared runtime/console telemetry. |
| `CMUL8_REDIS_URL` | required in private deployment | Redis readiness and wake-up transport. |
| `CMUL8_BACKUP_REFERENCE` | unset | Operator evidence reference for the coordinated production backup. |
| `CMUL8_RESTORE_TEST_REFERENCE` | unset | Operator evidence reference for the latest successful restore drill. |
| `SIMULACRA_SANDBOX` | `auto` locally, `worktree` in Compose | Builder execution isolation mode. |

See [`.env.example`](.env.example) for optional Clerk, SIEM, Firecrawl, machine sandbox, and public URL settings.

## Authorization model

Tenant/project access is the outer boundary. Durable Project Room membership controls mutations inside a project.

| Room role | Effective V0 access |
| --- | --- |
| `owner` | Full room, graph, build, membership, task, and review authority |
| `admin` | Full operational authority, including graph approval and invitations |
| `member` | Participate in tasks and comments; cannot approve graph mutations |
| `viewer` | Read-only room access |
| `reviewer` | Review tasks according to the durable workflow policy |
| `approver` | Approve/reject review-stage tasks according to policy |

Graph proposal, approval, build, repair, and iteration recheck the initiating actor at the mutation boundary and again inside long-running jobs. A demoted user cannot promote a stale model response into project state.

## Safety properties

- **Exact approval:** builders and workers are pinned to immutable Operation Graph revision hashes.
- **Revision isolation:** jobs and entity, workflow, task, approval, and action records cannot cross-execute under another revision.
- **Credential screening:** nested fields, camelCase keys, URLs, headers, query parameters, connector results, and error paths are screened before durable persistence.
- **Consequential-action boundary:** external writes require policy approval and deny self-approval unless the graph explicitly permits it.
- **Filesystem confinement:** graph staging/publication uses no-follow, descriptor-confined atomic writes.
- **Durable execution:** job terminal state is persisted independently of best-effort telemetry.
- **Read-only chat:** non-owner chat uses ephemeral provider and local sessions and cannot mutate build-driving project state.

## Development and verification

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[demo,dev]"
```

Install frontend dependencies:

```bash
npm ci --prefix apps/console
npm ci --prefix templates/internal-app
```

Run the release checks:

```bash
pytest -q
npm run build --prefix apps/console
npm run build --prefix templates/internal-app
git diff --check
```

The test suite covers the neutral product journey, exact graph approval, credential and symlink attacks, room-role matrices, multiplayer operations, mixed graph revisions, worker leases/retries/dead letters, telemetry failures, dynamic worker discovery, Codex JSONL protocol, and deployment contracts.

### Private or open-model deployment

The built-in executor can use a private open model without changing the Missions UI:

```bash
CMUL8_EXECUTION_BACKEND=codex
CMUL8_MODEL_PROVIDER=custom
CMUL8_MODEL=gpt-oss-120b
CMUL8_MODEL_BASE_URL=https://models.internal.example/v1
CMUL8_MODEL_API_KEY_ENV=CMUL8_MODEL_API_KEY
CMUL8_MODEL_API_KEY=replace-with-secret-manager-value
```

The endpoint must implement the Responses contract over HTTPS. The route and model ID are pinned into the admitted run; the credential value is read only by the executor and is never persisted in Mission evidence.

Enterprise deployments can bake another reviewed `MissionAgentExecutor` adapter into their image and add it to the source-controlled certified registry. Environment configuration may select a baked adapter, but it cannot import arbitrary application-process code. `JsonProcessMissionAgentExecutor` is the provider-neutral adapter: it launches `/opt/cmul8/executors/<backend>/bin/mission-executor mission-executor --stdio` behind the same one-turn filesystem sandbox, sends one versioned JSON request, requires a request/ack admission before every action, and accepts one bounded normalized result. Certification requires the baked adapter to keep model traffic inside its trusted runtime and enforce the admitted network policy for every model-invoked tool; deployment rejects adapters that do not declare that boundary. Every adapter receives only the admitted request, managed isolation, and its execution-session store; Mission permissions, approvals, artifacts, and verification remain outside the adapter.

Harness providers should start with the complete [Missions executor provider interface](docs/MISSION_EXECUTOR_PROVIDER_INTERFACE.md). It defines the JSON protocol, action-admission handshake, result schema, model/credential boundary, image layout, certified registry integration, and required release tests.

## Repository map

```text
apps/api/                     FastAPI control plane and CMUL8 product routes
apps/console/                 React multiplayer console
simulacra/collaboration/      Project Room domain and durable repository
simulacra/demo/               Project, builder, graph, job, and preview orchestration
simulacra/harnesses/          Codex, Prime, fake, and provider-neutral contracts
simulacra/operation_graph/    Schema validation, immutable revisions, approval, security
simulacra/runtime/            Workflow/action services, scheduler, worker, observability
templates/internal-app/       Neutral graph-driven React application floor
tests/                        Unit, integration, security, deployment, and V0 journey tests
docker-compose.yml            Single-VM private deployment
```

## Troubleshooting

### `Codex app-server executable not found`

Install the pinned CLI or set `CMUL8_CODEX_BIN` to an executable path:

```bash
npm install -g @openai/codex@0.148.0
codex --version
```

### Codex cannot initialize its state directory

Ensure `CODEX_HOME` is writable. Compose sets it to the persistent `/app/data/codex` volume.

### Build returns an approval or revision conflict

Open the Project Room, review the current Operation Graph head, and approve that exact revision. Creating a newer revision intentionally invalidates approval of the previous head for new builds.

### Worker is not ready

Check Redis reachability and shared roots:

```bash
docker compose ps
docker compose logs worker
docker compose exec redis redis-cli ping
```

The API and worker must share the same `CMUL8_RUNTIME_ROOT`, `CMUL8_TELEMETRY_ROOT`, and runs volume.

### Login does not work

Confirm `SIMULACRA_AUTH_REQUIRED=1` and that the bootstrap email/password were present when the identity store was first initialized. Changing environment variables does not silently replace an existing durable owner.

## Current V0 limits

- The supported deployment is one Docker Compose host, not Kubernetes or a multi-region control plane.
- TLS termination, DNS, backup scheduling, image signing, and disaster-recovery automation remain operator responsibilities.
- Redis is not the durable job database; it is a wake-up/readiness transport.
- Connectors require injected executors and opaque credential references. CMUL8 does not ship broad third-party write access by default.
- PostgreSQL/data-volume backup, upgrade, restore, and rollback must be rehearsed in the target environment before production use.
- Warehouse-native connectors and large-scale multi-worker scheduling are beyond V0.

## More documentation

| Document | Purpose |
| --- | --- |
| [Usable V0 checklist](docs/V0_USABLE_CHECKLIST.md) | Product and deployment exit criteria |
| [CMUL8 V0 foundation](docs/architecture/CMUL8_V0_FOUNDATION.md) | Control-plane and harness boundaries |
| [Product specification](docs/PRODUCT_SPEC.md) | Broader direction; some historical engine language predates the Codex-default V0 |
| [Roadmap](docs/ROADMAP.md) | Longer-term data plane and product roadmap |

## License

The Python package metadata declares MIT. Add the repository license file required by your distribution process before publishing binaries or source releases.
