# App Maker Contract

**Product law.** The **main chat is Prime**. Simulacra is **infra + observer**: sources, gates, templates, preview, jobs, ship. The user and Prime steer; Simulacra runs structured requests and explicit user actions (Build / Ship / upload).

## One sentence

User states intent → picks a **format** → **Prime chat** opens (honest about the data room) → user steers (sources, research, scope) → user hits **Build** → Simulacra scaffolds + Prime customizes → **Built** preview → chat still goes to Prime (iterate when Prime requests it) → user **Ships**. Never fake. Never silent heuristics dressed as builds.

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
| **Agent** | Conversation; what to do next; when to ask for sources/research; when to request Build or iterate |
| **Simulacra** | Auth, tenants, sources, extract, gates, sandbox, **format templates**, preview URL, jobs, ship, audit — **observes** the agent’s structured `request` **and intervenes** on usual product actions (e.g. research file written → promote into data room) |
| **User** | Intent, **format**, chat, uploads, explicit **Build** / **Ship** |

**Observer law.** Simulacra does not invent chat replies. It does notice when the agent did something that product infra must complete — research saved outside the data room, iterate requested, gates after Build — and runs that infra without waiting for the user to babysit files.

## Loop (memorize)

```
Create (+ format) → Prime chat (user steers)
                 → Build (user) → scaffold + builder → Built preview
                 → Drive (chat → Prime → request=iterate → infra)
                 → Ship
```

| Step | What happens | User sees |
|------|----------------|-----------|
| **Create** | Scan data room → open **Prime** turn. No silent auto-build. | Status chip + agent reply (what it has / needs) |
| **Steer** | Every chat message → Prime (`reply` + `request`). Simulacra shows sources/job state. | One continuous Agent chat |
| **Build** | User hits Build → gates → format template → **builder customizes** → preview. Clears `prime.request`. | **Building…** → **Built** + Preview |
| **Drive** | Chat → Prime; if `request=iterate` and artifact exists → `iterate_run` | Thinking → Preview updates → reply |
| **Ship** | Gates pass → `deployed=true` + stable preview URL + chat receipt | **Shipped** + shareable link |
| **Versions** | Restore an earlier preview by name (Added chart, First build, …). No forks. | Preview restored |
| **Start over** | Escape hatch — wipe back to blank template, build again | Same as create deepen |

## Prime chat envelope

Every Prime chat turn returns JSON (Simulacra observes; does not invent replies):

| Field | Meaning |
|-------|---------|
| `reply` | Markdown for the user |
| `title` / `subtitle` | Optional product naming |
| `request` | `await_user` \| `build` \| `iterate` \| `research` |
| `brief` | Optional instruction for iterate/research |

- `await_user` — conversation only.
- `build` — surface “Agent asked for Build”; user must still press **Build**.
- `iterate` — only when phase is ready; Simulacra starts `iterate_run`.
- `research` — Simulacra **observes** agent-written research (`*research*`, topic packs under `work/`) and **promotes them into the data room** + wires `research.json` for reports. Never invent finished research as fact.

## Chat rules (critical)

1. **Main chat is always Prime** — one API path (`POST /chat`). No Plan-vs-Agent dual brains.
2. Simulacra does **not** route on `is_question_only` heuristics for product chat.
3. Style chips still patch tokens live on the preview.
4. **Never** pretend a heuristic rename was an agent build.
5. **One job at a time** — Stop unlocks UI; last good preview kept.

## What Ship is (and is not)

- **Is:** Mark this preview **approved**, keep the same-origin URL, tell the user in chat, show **Shipped**.
- **Is not:** Multi-region cloud deploy, CDN, custom domain (yet). Do not imply otherwise in UI copy.

## Honesty chips

| State | Chip |
|-------|------|
| Before first Build / planning | **Plan** |
| Create in progress / agent missed (styles only) | **Draft** |
| Agent customized successfully (`source=prime`) | **Built** |
| Craft fallback personalized layout (`source=craft`) | **Built** — chat says craft applied because agent wrote no files |
| User shipped | **Shipped** |
| Stopped / error | **Stopped** / **Retry** |

## Job kinds

| Kind | Role |
|------|------|
| `agent_chat` | Prime chat turn (create open + every user message) |
| `plan_ask` | Alias / same bounds as `agent_chat` (compat) |
| `build_run` | First Build / Start over (agent) |
| `iterate_run` | Prime requested iterate — edit artifact (preserve prior work) |
| `bootstrap` | Legacy create scaffold (tests); not live create |

## Concurrency (multi-user)

Isolation is **per project**, not a global chat name:

| Layer | Rule |
|-------|------|
| **Session** | Prime session dir = `runs/{project_id}/work/prime-session/`; stable name `chat-{project_id}` for RLM resume within that project only |
| **Jobs** | At most **one running job per project**. Parallel users = parallel projects |
| **Tenant cap** | `tenant.policy.max_concurrent_jobs` (default **2**) — extra starts get conflict |
| **Host cap** | `SIMULACRA_MAX_RUNNING_JOBS` (default **48**) across the process |
| **Events** | SSE subscribers are keyed by `project_id`; faint progress lines are project-scoped |

**Scale reality (≈1000 users × long chats):** many idle users are cheap (no job). Concurrent **active turns** are bounded by host + tenant caps; excess get a clear “busy / workspace limit” error rather than silently sharing sessions. True 1k simultaneous LLM turns needs horizontal workers + shared job queue — not this single-process in-memory map.

## Edge cases

| Case | Behavior |
|------|----------|
| Empty / unrelated sources on create | Prime chat is honest; user may upload, sample pack, or ask to research. Build when user is ready. |
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
- Simulacra owning chat replies or Plan-vs-Agent dual backends
