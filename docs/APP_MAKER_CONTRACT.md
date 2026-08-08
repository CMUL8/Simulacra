# App Maker Contract

**Product law.** Simulacra is the **infra + scaffold**. The builder agent does the **building**. The user **drives the agent** in chat.

## One sentence

User states intent → picks a **format** (app / report / slides / one-pager) → Simulacra scaffolds **behind the scenes** and the builder customizes in one create job → user lands on a **Built** preview → user **chats to drive the agent** → user **Ships**. Never fake. Never silent heuristics dressed as builds.

## Formats (same loop)

| Kind | Template | What the draft is |
|------|----------|-------------------|
| `data_app` | `templates/internal-app` | Interactive command center |
| `report` | `templates/report` | Long-form HTML document |
| `slides` | `templates/slides` | Full-viewport multi-page HTML deck |
| `one_pager` | `templates/one-pager` | Single printable sheet |

Format is chosen on create (`artifact_kind`). Prompt keywords can hint; UI selection wins. Design brief IA (`must_have` / `must_not`) and builder craft skills switch with the format. Preview/ship path is unchanged (Vite → `dist` → same-origin URL).

## Roles

| Who | Owns |
|-----|------|
| **Simulacra** | Auth, tenants, sources, extract, gates, sandbox, **format templates**, preview URL, jobs, ship flag, audit |
| **Builder agent** | All durable edits to `app/src/*` (layout, style, viz, copy) for the chosen format |
| **User** | Intent, **format**, style chips, chat directions, Build, Ship |

## Loop (memorize)

```
Create (+ format) → (scaffold + builder, behind the scenes) → Built preview
                 → Drive (every change chat → agent iterate, preview refreshes)
                 → Ship (approve this build + share URL)
```

| Step | What happens | User sees |
|------|----------------|-----------|
| **Create** | Scan → gates → copy format template → **builder customizes** → preview. Template is not a user-facing wait. | **Building…** → **Built** + Preview |
| **Drive** | Chat that asks for a change → `iterate_run` → agent edits existing artifact (**does not wipe** prior agent work) | Thinking → Preview updates → reply |
| **Ask** | Pure questions only (`?` / what / why…) → short Q&A, **no file edits** | Answer in chat |
| **Ship** | Gates pass → `deployed=true` + stable preview URL + chat receipt | **Shipped** + shareable link |
| **Rebuild from draft** | Escape hatch — wipe back to template, agent builds again | Same as create deepen |

## Chat rules (critical)

1. **After create (Built)** — default is **drive the agent**. Almost every send that is not a pure question starts an agent iterate job.
2. Style chips still patch tokens live on the preview.
3. **Never** pretend a heuristic rename was an agent build.
4. **One job at a time** — Stop unlocks UI; last good preview kept.

## What Ship is (and is not)

- **Is:** Mark this preview **approved**, keep the same-origin URL, tell the user in chat, show **Shipped**.
- **Is not:** Multi-region cloud deploy, CDN, custom domain (yet). Do not imply otherwise in UI copy.

## Honesty chips

| State | Chip |
|-------|------|
| Create in progress / agent missed (styles only) | **Draft** |
| Agent customized successfully (`source=prime`) | **Built** |
| Craft fallback personalized layout (`source=craft`) | **Built** — chat says craft applied because agent wrote no files |
| User shipped | **Shipped** |
| Stopped / error | **Stopped** / **Retry** |

## Job kinds

| Kind | Role |
|------|------|
| `bootstrap` | Create: scaffold + builder customize |
| `plan_ask` | Plan Q&A |
| `build_run` | First Build / Rebuild from draft (agent) |
| `iterate_run` | Chat-driven agent edit (preserve prior work) |
| `iterate_ask` | Question-only |

## Edge cases

| Case | Behavior |
|------|----------|
| Agent off / fail on Build | Craft personalizer stamps format + brief when possible → **Built** (`craft`); else Draft + retry |
| Agent narrates, zero App.tsx diffs | One steered retry → then craft fallback (never claim prime) |
| Agent fail on iterate | Last good preview kept; honest “builder didn’t finish”; no fake heuristic success |
| Ship before gates pass | Blocked |
| Ship then chat iterate | Allowed — agent keeps editing; still Shipped until they care |
| Double job | Conflict — one builder |
| Format switch mid-project | Not supported in v1 — create a new project |

## Non-goals

- Replacing the agent with Simulacra heuristics for UI work
- Silent “success” when no files changed
- Implying Ship = production multi-region
- Treating formats as separate products — one maker loop, four crafts
