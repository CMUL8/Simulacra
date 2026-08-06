# Simulacra

Python wrapper around [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) for programmatic and multi-agent workflows.

Prime Agent is vendored under `./prime-agent` (clone of `PrimeIntellect-ai/prime-agent`). Simulacra drives it through **RPC mode** (`prime-agent --mode rpc`) over stdin/stdout JSONL.

## Prime Agent analysis (short)

Prime Agent is a self-improving **RLM** (Recursive Language Model) coding/research agent. Core ideas:

| Concept | What it means |
| --- | --- |
| RLM / IPython | The model’s primary tool is a persistent IPython REPL. File ops, shell, skills, and subagents are programmatic. |
| Continual Harness | Supplemental prompts, memories, skill descriptions, and subagent specs persist and can be refined (`/refine`) without rewriting the base system prompt. |
| Daemon + workers | Interactive UI is a client. A supervisor routes to session workers. Workers own `AgentSessionRuntime`, kernels, and child RLM runtimes. |
| Long-running | Detach/reattach, goals, heartbeats, schedules, compaction, autonomous budgets. |
| Headless APIs | `--mode json` (event stream) and `--mode rpc` (bidirectional JSONL). TS users can also import `AgentSession` / `RpcClient` from `@earendil-works/pi-coding-agent`. |

Package map inside the clone:

- `packages/ai` — unified LLM provider streaming
- `packages/agent` — general agent core / transport
- `packages/tui` — terminal UI library
- `packages/coding-agent` — CLI, daemon, RLM, sessions, RPC/JSON modes
- `prime-agent-runtime` — Python IPython kernel side

**Trust model:** workers/kernels are lifecycle isolation, not a security sandbox. They run with your user permissions.

**Best integration surface for Simulacra:** RPC mode. It already has a typed TS client (`packages/coding-agent/src/modes/rpc/rpc-client.ts`). This repo mirrors that client in Python and adds a higher-level `Agent` / `AgentPool` API.

## Install

```bash
# from Simulacra root
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Prime Agent itself needs Node ≥ 22.8 and auth (API key or `/login`). For the local clone:

```bash
cd prime-agent
npm ci
# optional: ensure kernel venv
./prime-agent.sh doctor
```

Or install the public binary and set:

```bash
export PRIME_AGENT_BIN=prime-agent
# or absolute path to the installer binary
```

## Usage

### Single agent

```python
import asyncio
from simulacra import Agent

async def main():
    async with Agent(cwd=".", provider="anthropic", no_session=True) as agent:
        print(await agent.ask("Summarize this repository in 3 bullets"))

asyncio.run(main())
```

### Parallel pool

```python
import asyncio
from simulacra import AgentPool, PoolTask

async def main():
    pool = AgentPool(concurrency=4, no_session=True)
    results = await pool.map([
        PoolTask(prompt="You are a price-sensitive shopper. Would you buy X at $12?", agent_name="p1"),
        PoolTask(prompt="You are brand-loyal. Would you buy X at $12?", agent_name="p2"),
    ])
    for r in results:
        print(r.task.agent_name, r.result.text if r.ok else r.error)

asyncio.run(main())
```

### CLI

```bash
simulacra which
simulacra run "Reply with exactly: ok" --no-session
```

## Layout

```
Simulacra/
  prime-agent/          # cloned upstream
  simulacra/            # Python wrapper package
    resolve.py          # find launcher (env / local clone / PATH)
    rpc.py              # async JSONL RPC client
    agent.py            # high-level Agent
    pool.py             # bounded concurrency pool
    cli.py              # simulacra which|run
  examples/
  tests/
```

## Notes

- RPC framing is strict JSONL (`\\n` only). The client does not use `readline`.
- `AgentPool` starts one Prime Agent process per task (isolated kernels/sessions). Cap `concurrency` to your provider rate limits.
- This wrapper does not sandbox execution. Same warning as upstream applies.
