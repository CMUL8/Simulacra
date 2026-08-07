# Simulacra

**Governed vibe coding for internal data apps** — natural language to production analytics apps on your warehouse, with persistent execution, eval gates, and audit trails.

Simulacra turns **data rooms, tables, and (later) warehouses** into **verified artifacts and deployable internal apps**. Execution runs on [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) **unforked**; Simulacra owns branding, policy, the run contract, eval gates, audit packs, and deploy.

> **Near-term focus:** shared research on governed folders of unstructured (and mixed) data → reproducible tabular outputs + audit. Warehouse connectors and app deploy follow on the same loop.

## Docs

| Doc | Purpose |
| --- | --- |
| [Product spec](docs/PRODUCT_SPEC.md) | Brand, architecture, data plane, run contract, gates |
| [Roadmap](docs/ROADMAP.md) | Phased plan (Foundation → Data rooms → Tabular → Warehouse → Apps) |

## How it fits together

```text
You / Simulacra CLI
    → policy + run workspace (inputs / outputs / audit)
        → Prime Agent (pure): IPython · rlm · daemon · sessions
            → gated artifacts → optional internal app deploy
```

| Layer | Owner |
| --- | --- |
| Agent runtime | Prime Agent |
| Governance, manifest, gates, audit, deploy | Simulacra |

## Progressive data plane

1. **Unstructured data rooms** — folders of PDFs, docs, logs, CSV/JSON  
2. **Tabular lab** — Parquet / DuckDB analytics on extracts  
3. **Warehouse** — read-only certified data (Snowflake / BigQuery / Databricks — pick one)  
4. **Deployable apps** — template-bound internal apps (Streamlit first) with approve-to-ship  

## Demo (web app)

**One prompt + fixture data room → live React data app**, with chat follow-ups.

```bash
chmod +x scripts/demo.sh
./scripts/demo.sh
```

Open **http://127.0.0.1:5173**

Sign in with bootstrap `admin@localhost` / `simulacra-admin-change-me` (or create a workspace). Auth is on by default (`SIMULACRA_AUTH_REQUIRED=1`).

1. Describe your app → **Build app**
2. Chat to refine (“add search”, “group by vendor”, “rename to …”)
3. **Approve & deploy** when gates pass

Stack: FastAPI (`apps/api`) · Simulacra console (`apps/console`) · generated app (`templates/internal-app` → `runs/<id>/app`).

**Multi-tenant / enterprise:** workspaces, RBAC (owner/admin/member/viewer), API keys, project quotas, sandbox policy, and audit trails. See Admin in the console. Deploy with `docker compose up --build` or Fly (`fly.toml`).

Postgres identity: set `SIMULACRA_DATABASE_URL` (compose includes Postgres). Sandbox: `SIMULACRA_SANDBOX=gvisor|machine|docker|worktree`. SIEM: `GET /admin/audit/export?format=cef|json|hec` and optional `SIMULACRA_SIEM_WEBHOOK`.

## Status

Early. Spec and roadmap are in `docs/`. A thin Python RPC helper under `simulacra/` exists from exploration; the product path is **Prime-pure + Simulacra control plane**, not a second agent framework.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Prime Agent: install via upstream installer, or keep a local clone at `./prime-agent` (gitignored) and point `PRIME_AGENT_BIN` at it.

## Trust model

Prime workers/kernels are **lifecycle isolation, not a security sandbox**. Simulacra will add worktree jails, gates, and (before sensitive data) stronger sandboxing. See the [spec](docs/PRODUCT_SPEC.md#91-trust-model).

## License

TBD. Prime Agent is MIT; respect its license when redistributing.
