# App Maker Contract

**Product law.** Simulacra is the **infra + scaffold**. The builder agent does the **building**. The user **drives the agent** in chat.

## One sentence

User states intent → Simulacra scaffolds a **draft preview fast** → user **Builds** once so the agent customizes → user **chats to drive the agent** back and forth → user **Ships** an approved link. Never fake. Never silent heuristics dressed as builds.

## Roles

| Who | Owns |
|-----|------|
| **Simulacra** | Auth, tenants, sources, extract, gates, sandbox, template scaffold, preview URL, jobs, ship flag, audit |
| **Builder agent** | All durable edits to `app/src/*` (layout, style, viz, copy) |
| **User** | Intent, style chips, chat directions, Build, Ship |

## Loop (memorize)

```
Create → Scaffold (draft) → Build app (agent customizes once from draft)
       → Drive (every change chat → agent iterate, preview refreshes)
       → Ship (approve this build + share URL)
```

| Step | What happens | User sees |
|------|----------------|-----------|
| **Scaffold** | Scan → parquet → gates → copy template → same-origin preview. **No agent file edits.** | Plan + **Draft** + Preview |
| **Build app** | Re-sync craft template **once**, then agent rewrites app under the design brief | **Building…** → **Built** (or honest failure) |
| **Drive** | Chat that asks for a change → `iterate_run` → agent edits existing app (**does not wipe** prior agent work) | Thinking → Preview updates → reply |
| **Ask** | Pure questions only (`?` / what / why…) → short Q&A, **no file edits** | Answer in chat |
| **Ship** | Gates pass → `deployed=true` + stable preview URL + chat receipt | **Shipped** + shareable link |
| **Rebuild from draft** | Explicit escape hatch — wipe back to template, agent builds again | Same as Build app |

## Chat rules (critical)

1. **Plan phase** — chat refines plan / brief only. Does **not** edit app code. Styles chips apply tokens to the draft.
2. **After Build** — default is **drive the agent**. Almost every send that is not a pure question starts an agent iterate job.
3. **Never** pretend a heuristic rename was an agent build.
4. **One job at a time** — Stop unlocks UI; last good preview kept.

## What Ship is (and is not)

- **Is:** Mark this preview **approved**, keep the same-origin URL, tell the user in chat, show **Shipped**.
- **Is not:** Multi-region cloud deploy, CDN, custom domain (yet). Do not imply otherwise in UI copy.

## Honesty chips

| State | Chip |
|-------|------|
| Scaffold / plan / agent missed (styles only) | **Draft** |
| Agent customized successfully (`source=prime`) | **Built** |
| Craft fallback personalized layout (`source=craft`) | **Built** — chat says craft applied because agent wrote no files |
| User shipped | **Shipped** |
| Stopped / error | **Stopped** / **Retry** |

## Job kinds

| Kind | Role |
|------|------|
| `bootstrap` | Scaffold only |
| `plan_ask` | Plan Q&A |
| `build_run` | First Build / Rebuild from draft (agent) |
| `iterate_run` | Chat-driven agent edit (preserve prior work) |
| `iterate_ask` | Question-only |

## Edge cases

| Case | Behavior |
|------|----------|
| Agent off / fail on Build | Craft personalizer rewrites `App.tsx` from brief when possible → **Built** (`craft`); else Draft + retry |
| Agent narrates, zero App.tsx diffs | One steered retry → then craft fallback (never claim prime) |
| Agent fail on iterate | Last good preview kept; honest “builder didn’t finish”; no fake heuristic success |
| Ship before gates pass | Blocked |
| Ship then chat iterate | Allowed — agent keeps editing; still Shipped until they care |
| Double job | Conflict — one builder |

## Non-goals

- Replacing the agent with Simulacra heuristics for UI work
- Silent “success” when no files changed
- Implying Ship = production multi-region
