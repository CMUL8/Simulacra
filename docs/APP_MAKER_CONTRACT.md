# App Maker Contract

**Product law for Simulacra as a consumer / prosumer app maker.**  
Supersedes the slow “plan forever → Approve → maybe Prime → preview” harness for the maker loop.  
Policy, gates, audit, and tenants still follow `PRODUCT_SPEC.md`.

## One sentence

User types intent → sees a **live preview they trust in under ~60s** for known data patterns → can **stop**, **refine**, or **deepen with Prime** → then **ship**. Never stuck. Never fake.

## Hard rules

1. **Bootstrap first** — first preview does **not** wait on Prime.
2. **One job, one truth** — UI busy iff a live in-process job exists (`GET …/job` `live:true`); ghost `state.job=running` after restart must heal.
3. **Stop always works** — cancel is idempotent; UI unlocks in &lt;200ms even if Prime is still dying.
4. **No silent heuristics** — chips must say `Template` / `Heuristic` / `Prime` / `Fallback` / `Stopped`. Never claim Prime built it when it did not.
5. **Prime is deepen** — taste, layout, iteration under the design brief. Explicit user action (or clear auto-deepen later); not required for first preview.
6. **Deploy ≠ checkbox forever** — today `deployed=true` keeps the preview URL; call the control **Ship** / **Approve deploy**, not “we shipped to multi-region cloud.”

## Phases

| Phase | Owner | User sees |
|-------|--------|-----------|
| **Create** | API &lt;500ms | Project id, user prompt in chat, bootstrap job `live` |
| **Bootstrap** | Simulacra | Scan → parquet → gates → template sync → preview URL → phase `ready`, source `template` |
| **Refine** | Chat + design brief | Style chips, Q&A, no full rebuild |
| **Deepen** | Prime (`build_run` / `iterate_run`) | “Improve with Prime”; honesty → `prime` or honest fallback |
| **Ship** | Simulacra | Gates pass → `deployed` |

## Job kinds

| Kind | Role |
|------|------|
| `bootstrap` | Fast maker path — no Prime |
| `plan_ask` | Optional plan Q&A (Prime ask or heuristic) |
| `build_run` | Deepen / customize with Prime |
| `iterate_run` | UI deltas with Prime |
| `iterate_ask` | Short Q&A |

## Edge cases (must)

| Case | Behavior |
|------|----------|
| Stop anytime | UI idle immediately; last good preview kept |
| Restart mid-job | Reopen → not Thinking; `live:false` clears busy |
| Prime down | Bootstrap still ships preview; chip **Template**; CTA **Improve with Prime** |
| Gates fail | No fake preview; clear failure message |
| Empty sources | Create/bootstrap fails clearly |
| Double deepen | `JobConflict` — one builder |
| Cancel when idle | 200 + `already_idle`, never 409 strand |

## Honesty vocabulary

| `prime.source` / message `source` | Chip |
|-----------------------------------|------|
| `prime` | Prime |
| `template` | Template |
| `heuristic` | Heuristic |
| `error` | Fallback |
| `cancelled` | Stopped |

## Non-goals (for this contract)

- Multi-region cloud deploy
- Replacing the filesystem run store (yet)
- Silent Prime retries that look like success
