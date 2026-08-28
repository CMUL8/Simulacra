# Missions repository working agreement

This repository is building **Missions**: a shared workplace where humans set outcomes, agents carry work forward, and humans steer decisions, verify evidence, and approve what ships.

## Source of truth

- For workplace product work, read the relevant sections of `docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md` before editing.
- Treat its public contracts, ownership transfers, RED/GREEN tests, and release gates as binding.
- When code and the plan disagree, stop and resolve the product contract instead of silently inventing a third behavior.

## Product boundaries

- Say **humans**, never “people,” in product copy describing human-agent collaboration.
- Do not expose Codex, model, provider, runtime, MCP, graph, worker, host, path, raw tool, or raw exception details in the normal product UI or public API.
- Customer-visible agents are durable Mission identities with explicit roles, scopes, autonomy, and audit history. Do not replace them with invisible nested runtime subagents.
- Durable Mission, conversation, work, approval, and evidence records are the system of record. Model threads and streamed events are execution inputs, not authoritative product state.
- Consequential writes and publication require the exact permission and verification boundaries defined in the plan.

## Delivery protocol

For each implementation wave:

1. State the outcome, constraints, owned files, acceptance tests, and evidence required.
2. Inspect the existing diff and preserve all unrelated or concurrent edits.
3. Write the named RED tests first, implement the smallest complete vertical GREEN slice, then refactor.
4. Keep one writer per file. Parallelize independent reads, exploration, browser reproduction, test analysis, and genuinely disjoint write sets only.
5. The primary agent owns architecture, contract decisions, integration, final test execution, and acceptance.
6. Use a fresh read-only review for authorization, persistence, concurrency, preview security, runtime isolation, or release-boundary changes.
7. Do not mark a wave complete until its focused command and applicable full regression/build/browser gates pass.

## User-facing progress updates

- Lead with the product capability added or changed: what a human can now do, where it works, and why it matters.
- Describe completed product areas and the remaining path to a usable release in plain language.
- Keep implementation mechanics, internal state names, race conditions, test counts, and storage details out of routine streamed updates.
- Mention technical detail only when it changes product behavior, creates a user-visible limitation, requires a decision, or represents a real release risk.
- Translate verification into confidence statements such as "assignments survive retries without running twice"; include raw commands or counts only in the final engineering evidence or when explicitly requested.
- Apply this communication style to the primary agent, implementers, explorers, and reviewers. Delegated agents should return technical evidence privately to the primary agent, which translates it before updating the user.

## Agent work packets

Every delegated implementation packet must include:

- `OBJECTIVE`: one measurable result.
- `FILES AND OWNERSHIP`: exact files and explicit exclusions.
- `INTERFACES`: frozen request, response, state, and event contracts.
- `CONSTRAINTS`: security, compatibility, UX, and concurrency boundaries.
- `VERIFICATION`: exact tests and evidence to return.

Explorers and reviewers are read-only. Implementers must not revert other contributors' edits and must return changed files, tests run, results, and residual risks.

## Verification defaults

- Backend: `uv run pytest -q`
- Console unit/type/build: `npm --prefix apps/console run test:unit`, `npm --prefix apps/console run typecheck`, `npm --prefix apps/console run build`
- Browser and visual checks: use the exact Playwright commands and screenshot matrix defined by the active wave.
- Before handoff: run `git diff --check` and inspect the full accumulated diff, not only the last agent's files.
