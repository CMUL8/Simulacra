# Missions Shared Workplace — Implementation and Reliability Plan

**Status:** Implementation specification
**Audience:** Product, design, frontend, API, runtime, QA, and release engineering
**Target:** Move the current Missions V0 from a capable Mission runner to a polished, reliable human-and-agent workplace with the interaction quality of mature collaboration products, while keeping execution infrastructure managed and invisible.
**Related:** [Missions V0](./MISSIONS_V0.md), [V0 usable checklist](./V0_USABLE_CHECKLIST.md), [architecture foundation](./architecture/CMUL8_V0_FOUNDATION.md)

---

## 1. Product decision

Missions is not a clone of a workspace/chat product. It borrows the successful collaboration grammar—shared conversation, task ownership, attention queues, files, presence, and durable history—and removes infrastructure the customer should not manage.

The product hierarchy is:

```text
Human account
└── Workspace / organization
    ├── Mission
    │   ├── Conversation
    │   ├── Work
    │   ├── Files
    │   └── Crew
    ├── Mission
    └── Mission
```

One Mission is the durable workplace for one outcome. It can contain multiple humans and multiple agents. Humans and agents communicate in one shared conversation, own work, exchange evidence, and complete a Mission through explicit human decisions.

The only public execution model is:

- Users define an agent's name, job, scope, and human-guidance boundary.
- Missions selects and operates the runtime and model route.
- Provider, model, credentials, process, computer, MCP, environment variables, raw tool logs, and filesystem paths never appear in the customer product.

### 1.1 Product promise

> Set the Mission. Agents carry it through; humans guide the decisions, verify the evidence, and approve what ships.

### 1.2 Success condition

A new workspace is successful when a human can:

1. Create a Mission from one outcome statement.
2. Add source files.
3. Accept or customize a proposed agent crew.
4. Invite one or more humans.
5. Assign work using natural language and `@mentions`.
6. Leave the product while agents continue working.
7. Return to a single attention queue showing exactly what needs a human.
8. Review the conversation, work history, sources, outputs, and evidence.
9. Approve or reject consequential actions and exact deliverable versions.
10. Schedule the Mission or trigger it from a condition.

No step may require knowledge of the managed runtime.

---

## 2. Current-state baseline

The implementation must extend the current system rather than create a parallel product.

### 2.1 Existing backend capabilities to preserve

- Durable Mission, agent, run, trigger, deliverable, approval, and trajectory records.
- Project-scoped multiplayer room with humans, presence, tasks, comments, reviews, and revision checks.
- Exact approved Operation Graph binding before execution.
- Interchangeable, deployment-owned agent execution boundary with Codex app-server as the first built-in certified executor and operator-controlled Responses-compatible model routing, including private open-model endpoints.
- Strict Mission and agent budgets.
- One live run per Mission across worker replicas.
- Durable leases, retry, cancellation boundaries, and recovery gates.
- Human checkpoints and producer self-verification denial.
- Immutable deliverable versions and exact-hash verification.
- Attempt-unique staged code output and verify-time promotion.
- Cron and condition triggers with durable occurrence idempotency.
- Bounded retention and descriptor-safe persistence.
- Recursive public-value secret screening and bounded trajectory export.

Primary modules:

- `simulacra/missions/models.py`
- `simulacra/missions/service.py`
- `simulacra/missions/worker.py`
- `simulacra/collaboration/service.py`
- `apps/api/mission_routes.py`
- `apps/api/cmul8_routes.py`

### 2.2 Existing frontend capabilities to preserve

- Outcome-first Mission creation and Mission cards.
- Mission Conversation, Work, and Files surfaces.
- Persistent Crew rail containing humans and agents.
- Role-first agent creation without runtime fields.
- Agent presets and recommended crew.
- Agent and human profile drawers.
- `@Agent`, `@Crew`, and human mention selection.
- Assignment creation from the composer.
- Agent work and durable handoffs returned into the Mission conversation.
- Work summaries for Needs you, In progress, Ready for review, Done, and Stopped.
- Human checkpoint and deliverable verification actions.
- Sources and versioned outputs.
- Mission automation editor.

Primary modules:

- `apps/console/src/components/Landing.tsx`
- `apps/console/src/components/AgentShell.tsx`
- `apps/console/src/features/missions/MissionPod.tsx`
- `apps/console/src/features/project-room/ProjectRoom.tsx`
- `apps/console/src/features/project-room/TaskBoard.tsx`
- `apps/console/src/features/activity/ActivityInbox.tsx`

### 2.3 Confirmed gaps

The following are not yet complete product systems:

- Workspace-level navigation for Missions, Needs you, Work, and Settings.
- Workspace-wide attention aggregation.
- Durable conversation threads and reactions.
- Saved messages.
- Global search.
- Push/email notification delivery and preferences.
- Workspace-wide task aggregation with board/list/filter persistence.
- Complete file preview, provenance, and secure download interactions.
- Polished first-workspace onboarding.
- Customer-facing connections management.
- Billing/usage surfaces.
- Full responsive/mobile interaction design.
- Browser E2E coverage of the complete multiplayer and background-work loop.

---

## 3. Target information architecture

### 3.1 Global application shell

The primary navigation contains exactly four destinations:

| Destination | Purpose | Badge |
| --- | --- | --- |
| **Missions** | Browse, create, filter, and resume Missions | Count of active Missions, optional |
| **Needs you** | Human attention inbox across every Mission | Unread/actionable count |
| **Work** | Workspace-wide assignments and run state | Active work count |
| **Settings** | Account, workspace humans, connections, notifications, billing | Only when action required |

Global utilities live in the header, not the primary navigation:

- Search / command palette
- Notifications
- Workspace switcher
- Human account menu
- New Mission

There are no public Computers, Runtime, Models, Providers, MCP, Diagnostics, Graph, or Observability destinations.

### 3.2 Mission shell

The Mission header always shows:

- Mission title
- concise outcome
- current state
- active human and agent avatar stack
- unresolved human-decision count
- Start/Stop/Resume action when applicable
- compact Mission details control

The persistent Mission navigation contains:

| Tab | Contents |
| --- | --- |
| **Conversation** | Human messages, agent messages, assignments, progress, approvals, outputs, system milestones |
| **Work** | Shared queue, status groups, board/list, filters, decisions |
| **Files** | Sources, outputs, evidence, versions, preview/download |
| **Crew** | Humans and agents, roles, presence, current and recent work |

Mission outcome, definition of done, execution readiness, access plan, and automation live in a details drawer. They are not separate top-level tabs.

### 3.3 Terminology contract

Use these terms everywhere:

| Internal term | Customer term |
| --- | --- |
| project | Mission |
| room | Mission workplace |
| task/run | Work item, unless technical distinction is essential |
| operation graph | Mission plan or Access plan |
| graph approval | Approve how the crew will work |
| artifact/deliverable | Output |
| trigger | Automation |
| runtime job | Agent work |
| actor | Human or agent |
| revision conflict | This changed elsewhere; latest version loaded |
| credential unavailable | Execution is not activated |

API and storage names may remain internal. Public payloads and UI text must follow this vocabulary.

---

## 4. Canonical behaviors

This section is normative. An implementation that differs requires an explicit product decision recorded in this document.

### 4.1 Create and enter a Mission

#### Frontend

1. The landing composer asks only: **“What should this Mission accomplish?”**
2. Sources are optional and attachable before creation.
3. Deliverable type is inferred behind the scenes; it is not required as a primary choice.
4. Submit immediately creates the durable Mission and opens Conversation.
5. If authentication is required, the draft and selected source files remain intact through login.
6. The first Mission shows a three-step readiness checklist:
   - Outcome defined
   - Crew added
   - How the crew works approved
7. The product proposes a small starter crew based on the outcome. Recommendations are drafts until a human selects **Add**.

#### Backend

1. Project bootstrap is the sole pre-project idempotency exception: it first reserves a project ID under tenant-scoped `(tenant_id, authenticated_human_actor_id, operation="workspace_bootstrap", client_request_id)`, then every child project/Mission/room/graph operation uses that reserved `project_id` under the ordinary full project-scoped identity. It must not require an unknown `project_id` to deduplicate bootstrap.
2. Create or recover the collaboration room with the workspace owner as a Mission owner.
3. Generate the proposed Operation Graph from the exact Mission contract.
4. Never queue agent work until an exact graph revision is approved.
5. Return a public bootstrap payload containing Mission, readiness, recommended crew, permissions, and public workspace state.
6. Retrying the same tenant-scoped bootstrap reservation returns the same reserved project and public bootstrap payload and cannot duplicate Mission, room, graph, or source ingestion.

#### Failure behavior

- If Mission creation succeeds but room or graph creation fails, return `provisioning` and resume asynchronously.
- Do not show a destructive generic error for a recoverable partial bootstrap.
- The UI polls the public bootstrap state and offers Retry only after the background retry budget is exhausted.
- No raw path, identifier dump, exception text, or infrastructure term is displayed.

### 4.2 Conversation

Conversation is the durable shared history of the Mission. It is not a transient model chat.

#### Message types

- `human_message`
- `agent_message`
- `assignment_created`
- `agent_started`
- `agent_progress`
- `agent_completed`
- `human_decision_required`
- `human_decision_recorded`
- `output_ready`
- `output_verified`
- `automation_event`
- `system_milestone`

Each public message has:

```json
{
  "id": "msg_...",
  "mission_id": "...",
  "kind": "human_message",
  "author": { "id": "...", "kind": "human", "display_name": "...", "avatar_url": null },
  "body": "...",
  "created_at": "...",
  "edited_at": null,
  "thread": { "reply_count": 0, "latest_replies": [] },
  "reactions": [],
  "saved": false,
  "links": { "work_item_id": null, "run_id": null, "output_id": null }
}
```

#### Frontend behavior

- Messages are rendered oldest-to-newest with stable day separators.
- The viewport follows new messages only when the human is already near the bottom.
- When the human is reading older history, new messages increment a floating **New messages** control.
- `@` opens a keyboard-accessible picker for agents, humans, and `@Crew`.
- Selecting an agent or `@Crew` defaults composer mode to **Assign work**.
- Mentioning only humans keeps composer mode as **Message**, unless manually changed.
- The composer previews who will work and which humans will review before submission.
- Assignment submission is disabled if no agent exists or the Mission plan is unapproved; the UI links directly to the missing setup step.
- Every message can open a thread, be saved, copied, and linked.
- Reactions are limited to a small supported set in V1; arbitrary emoji storage is deferred.
- Agent progress is product-level language. Raw tool calls and model reasoning are never displayed.
- Agent completion appears in Conversation with summary, outputs, and **Open work** / **Review output** actions.

#### Backend behavior

- Comments become durable conversation messages through a projection layer; do not build a second unconnected message store.
- Existing projected comments remain canonical in the comment store. Replies, supported reactions, and private saves target their stable comment message ID through records in `conversation_state.json`; the comment body is never copied. Existence, Mission scope, and current membership are rechecked while holding the collaboration write lock before changing that overlay.
- Message creation accepts the canonical `client_request_id` within the full server-derived `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)` identity. Duplicate full identities return the original message. The retired message-specific request-key spelling is not accepted, and no wire-compatibility alias exists.
- Mentions are parsed server-side against the Mission's current crew using stable member IDs submitted by the client. The server never trusts display-name parsing alone.
- An assignment and its originating message are created through the recoverable cross-store coordinator protocol in Section 13.3; implementation must not claim a single filesystem-atomic write across the collaboration and Mission stores.
- Threads reference a root message ID; maximum nesting depth is one.
- Reactions use `(message_id, actor_id, reaction)` uniqueness.
- Saved messages are private per human and never visible to other members.
- Edits create an audit record. Deletion is soft deletion with a durable tombstone; linked work/evidence is never removed.
- Message bodies and public metadata pass the existing secret-screening boundary before persistence and again before public response serialization.

### 4.3 Assigning humans and agents

#### Assignment semantics

- `@Agent` creates an ordered run assignment for that agent.
- Multiple agent mentions create an ordered handoff chain in mention order.
- `@Crew` assigns all active Mission agents using the Mission's stored crew order.
- Human mentions on an assignment create watchers/reviewers; they do not execute agent work.
- An assignment without an agent remains a human task and must not invoke the runtime.
- A message without **Assign work** never starts an agent even if it contains plain text resembling a handle.

#### Required request

```json
{
  "client_request_id": "uuid",
  "body": "Reconcile the August invoices and prepare the exception pack.",
  "mode": "assignment",
  "assignee_agent_ids": ["agent_1", "agent_2"],
  "reviewer_human_ids": ["user_1"],
  "source_message_id": null
}
```

#### Authorization

- Mission member: message and create human tasks.
- Mission owner/admin: add or deactivate agents, change Mission contract, approve access plan.
- Mission reviewer/approver/owner/admin: decide only when designated by the approval policy.
- Viewers: read only.
- No actor may self-approve an output they produced.

### 4.4 Crew

#### Agent creation

The default form contains exactly:

1. Name
2. Job / role
3. What should this agent own?
4. Scope
5. When should it ask a human?

Optional advanced controls may expose bounded tool-action and turn-time caps. They must not expose provider/model/runtime fields.

Agent records remain durable even when deactivated so prior conversation and evidence retain identity.

#### Agent profile

Display:

- Name and job
- Ready / queued / working / waiting for human / unavailable
- Current assignment
- Scope
- Human-guidance boundary
- Recent work and outputs
- Assign with `@Name`

Do not display:

- Runtime or model identity
- Provider
- Reasoning effort
- Environment variables or credentials
- Computer or host
- MCP/skills/plugins
- Raw command activity
- Local workspace paths

#### Human profile

Display name, avatar, Mission role, presence, current review responsibility, and recent Mission activity. Email is visible only to authorized workspace administrators.

#### Presence

- Client sends a heartbeat only while the Mission tab is visible.
- `online`: server-derived last-seen age `<=45` seconds.
- `away`: server-derived last-seen age `>45` and `<=180` seconds.
- `offline`: server-derived last-seen age `>180` seconds, no record, or a process restart before a new heartbeat.
- Presence is advisory and must never gate durable work or authorization.

### 4.5 Work

Work is a projection over collaboration tasks, Mission runs, approvals, and outputs. Do not duplicate these records into a new generic task database.

#### Canonical buckets

1. **Needs you** — unassigned, blocked, awaiting a human decision, or awaiting verification
2. **In progress** — queued, preparing, running, or actively owned human tasks
3. **Ready for review** — work submitted for review and outputs awaiting verification
4. **Done** — completed work with required decisions recorded
5. **Stopped** — failed, cancelled, expired, rejected, or closed

#### Views

- Grouped list is the default.
- Board view uses the same buckets and exact same records.
- View choice and filters are private per human and persisted.

#### Filters

- Mission
- State
- Assignee
- Creator
- Human/agent
- Needs my decision
- Updated time

#### Work-item details

Every work item shows:

- Plain-language title
- origin Mission
- creator
- current owner(s)
- status
- created/updated time
- originating conversation link
- dependent approval/output
- public progress summary
- allowed next actions

The server returns allowed actions. The client must not infer authorization solely from role labels.

#### Concurrency

- One live agent run per Mission remains the backend invariant.
- New assignments are visibly queued.
- `replace` never cancels a live invocation without coordinated interrupt acknowledgement.
- `merge` never mutates an already-bound live prompt; it creates a follow-up.
- Optimistic mutations include the record revision. Conflict reloads the latest item and retains the human's unsent note.

### 4.6 Needs you

Needs you is the workspace-wide human attention system.

#### Included events

- direct human mention
- assignment to the current human
- unassigned work in a Mission the human manages
- agent checkpoint waiting for the human
- output awaiting the human's verification
- access-plan approval
- failed/stopped run requiring retry authority
- invitation or workspace action requiring the human

#### Excluded events

- ordinary agent progress
- successful background activity with no requested human action
- raw runtime events
- presence changes

#### Behavior

- Items are ordered by priority, then event time.
- Opening an item marks it read, not completed.
- Completion occurs only when its underlying required action is resolved.
- **Mark read** never approves, verifies, rejects, retries, or changes work state.
- Resolved items leave the actionable filter but remain in All for the retention window.
- Unread count and actionable count are distinct fields.

#### Backend projection

Create a workspace attention endpoint that aggregates authorized Mission room inboxes without loading full evidence state:

```http
GET /workspace/attention?filter=actionable&cursor=...&limit=50
POST /workspace/attention/read
```

Response items contain deep links and server-computed allowed actions. Pagination order must be stable by `(priority, created_at, id)`.

### 4.7 Files

#### Structure

- **Sources** — human-provided or connected inputs
- **Outputs** — agent-produced deliverable candidates and verified versions
- **Evidence** — provenance, validation summary, and human decisions associated with an output

#### Frontend behavior

- Files use stable human names, type, size, upload time, uploader, and latest-use information.
- Preview is available for supported text, PDF, image, CSV, and generated HTML formats.
- Unsupported types show metadata and secure download only.
- Outputs display version and state: Awaiting verification, Verified, Rejected, Superseded.
- Verification requires opening the exact candidate or an explicit review summary; there is no one-click blind verification from a count badge.
- Code/app output remains staged until exact verification promotes it.
- **Open original message** navigates to the precise conversation event that introduced the file/output.

#### Backend behavior

- Use opaque file IDs in public routes, never relative filesystem paths.
- Download/preview requires current project membership and file-level authorization.
- Set `Content-Disposition`, `Content-Type`, `X-Content-Type-Options: nosniff`, CSP for rendered documents, and private cache headers.
- Range requests are allowed only for immutable file versions.
- Every output response binds version, hash, producer, run, source refs, and verification state.
- Re-check descriptor-safe path and exact hash at preview/download/verification boundaries.

### 4.8 Search and Saved

#### Search scope

Workspace search covers:

- Mission title and outcome
- human and agent display names/jobs
- conversation messages
- work titles and summaries
- source/output names and safe extracted text

Search must not index raw runtime logs, credentials, private Codex state, hidden filesystem content, or rejected secret-bearing text.

#### Search behavior

- `Cmd/Ctrl+K` opens search.
- Results are grouped by Mission, Conversation, Work, Files, and Crew.
- Filters: Mission, author, type, and date.
- Results return a highlighted safe snippet and an exact deep link.
- Authorization is applied before ranking and before count calculation.
- Index removal follows membership revocation and soft deletion within 60 seconds.

#### Saved

- A human can save/unsave a message or output reference.
- Saved state is private to that human.
- Saved items retain a tombstone if the original content becomes unavailable.
- Saved is a search filter and optional utility page, not primary navigation.

### 4.9 Notifications

#### Channels

- In-app: the authoritative `AttentionItem`/event projection, required and read through the existing attention endpoints; it is not an outbox delivery.
- Browser push: optional external delivery per human
- Email digest: optional external delivery per human

#### Preferences

- All actionable Mission events
- Mentions and decisions only
- Off
- Per-Mission mute
- Digest frequency: immediate, daily, off

#### Delivery rules

- Treat the source event as authoritative; idempotently project and repair its outbox rows with a durable projector cursor. Do not claim a same-transaction event/outbox write across JSON files.
- Delivery workers claim with leases and durable provider idempotency keys.
- At-least-once external transport is acceptable; an unsupported provider can redeliver after a crash and is not promised email exactly-once. In-app deduplication is instead by authoritative attention/event identity.
- Retries use capped exponential backoff and a dead-letter state visible to operators.
- Notification text is rendered from typed event data, not raw exception/model output.
- Muting delivery never removes the event from Needs you.

### 4.10 Automation

- Presets: daily, weekdays, weekly, monthly, and when a named condition is reported.
- Advanced cron remains available behind an expandable control.
- Every automation shows next run, last result, enabled state, and concurrency behavior.
- Editing an automation is revision checked.
- A due occurrence without an approved current Mission plan is not consumed.
- A user can disable future occurrences without cancelling live work.
- Automation results enter the same Conversation, Work, Files, and Needs-you projections as manual assignments.

---

## 5. Required backend extensions

### 5.1 Public aggregate read models

Add read-optimized projections rather than returning full Mission state for every global surface.

#### Workspace Mission summaries

```http
GET /missions?state=active&cursor=...&limit=50
```

Fields:

- id, title, outcome summary
- public state
- updated_at
- human_count, agent_count
- active_work_count
- needs_human_count
- verified_output_count
- current human permissions

##### W2 Mission-summary mapping (frozen)

The public Mission identity is the existing `project_id`; the separately
persisted `Mission.id` is never used as a route or aggregate identity. Room
membership is checked before a Mission record, count, or pagination candidate
is loaded. A same-tenant project without current room membership is therefore
indistinguishable from an absent aggregate row.

| Persisted `Mission.status` | Public `public_state` | Included by `state=active` |
| --- | --- | --- |
| `draft` | `draft` | yes |
| `ready` | `ready` | yes |
| `running` | `active` | yes |
| `waiting_for_human` | `needs_human` | yes |
| `paused` | `paused` | yes |
| `blocked` | `needs_human` | yes |
| `completed` | `completed` | no |
| `failed` | `stopped` | yes |
| `archived` | `archived` | no |

`state=all` includes every mapped state. W2 accepts only `active|all`; later
facets such as Needs you, Scheduled, and Completed are client views over this
frozen mapping until a separately versioned endpoint contract adds them.

`current_human_permissions` is a display hint, not an authorization decision.
It uses only this vocabulary and order; every mutation still reauthorizes on
the server:

| Current room role | Public permissions |
| --- | --- |
| `viewer` | `view_mission` |
| `member` | `view_mission`, `message`, `assign_work` |
| `reviewer`, `approver` | `view_mission`, `message`, `assign_work`, `review_work` |
| `owner`, `admin` | `view_mission`, `message`, `assign_work`, `review_work`, `decide_checkpoint`, `manage_mission`, `manage_crew`, `manage_automation`, `approve_plan` |

Unknown roles are denied rather than projected. Item-specific allowed actions
may be narrower because designation, source revision, or current state is
authoritative at the action endpoint. Append `verify_output` for any current
room member who is the Mission `owner_id` or is in `verifier_ids`; room role by
itself never grants that permission.

#### Workspace Work

```http
GET /workspace/work?bucket=needs_you&mission_id=...&assignee_id=...&cursor=...&limit=50
```

The endpoint projects tasks, runs, approvals, and outputs into one discriminated union. Each record retains its source type and revision.

#### Workspace attention

As specified in 4.6.

##### W2 attention projection and receipt mapping (frozen)

Attention is a derived, authorized read model. Lower numeric priority is more
urgent. Each logical source/recipient pair has one stable deterministic ID;
source revision changes update that row and never manufacture a new unread
item or lose its resolved history. It never contains a filesystem or execution
identifier. `source_event_id` contains the causative collaboration event ID
when one exists and otherwise the stable canonical synthetic source key shown
below. Synthetic keys are server-created public identities, not paths.

| Public type | Authoritative source / `source_event_id` | Recipient predicate | `actionable` while | Priority | Fixed title; bounded summary source | Deep link | Allowed actions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `mention` | `comment.created` collaboration event / event ID | current human is in `mention_ids` | never; mentions are unread information in All, not unresolved decisions | 50 | `{human} mentioned you`; screened comment body | `/missions/{project_id}?tab=conversation&attention={attention_id}` | `open` |
| `assignment` | causative `task.created`/`task.claimed` collaboration event / event ID | event `assignee_id` is current human | current Task owner is still that human and state is not `done|failed|cancelled` | 30 | `Work assigned to you`; screened task title | `/missions/{project_id}?tab=work&item={task_id}` | `open`, plus `update_work` only while actionable |
| `unassigned_work` | causative Task creation event / event ID, otherwise `task:{task_id}` | current role is `owner|admin`; this remains the row's audience after the Task is claimed | current Task owner is absent and state is not `done|failed|cancelled` | 20 | `Work needs an owner`; screened task title | `/missions/{project_id}?tab=work&item={task_id}` | `open`, plus `claim_work` only while actionable |
| `decision_required` | Mission approval / `approval:{approval_id}` | current role is `owner|admin` under the current checkpoint endpoint | approval status is `pending` | 10 | `Decision needed`; fixed copy selected from the public approval code | `/missions/{project_id}?tab=conversation&approval={approval_id}` | `open`, plus `decide_checkpoint` only while actionable |
| `output_verification` | Deliverable / `deliverable:{deliverable_id}` | current human is the Mission owner or is in `verifier_ids` | deliverable state is `awaiting_verification` | 15 | `Output ready to verify`; screened deliverable name | `/missions/{project_id}?tab=files&output={deliverable_id}` | `open`, plus `verify_output` only while actionable |
| `plan_approval` | each retained proposed Mission-plan revision / `mission-plan:{project_id}:{public_revision}` | current role is `owner|admin` | that exact revision is the current head and has no approval decision; superseded revisions remain non-actionable history in All | 5 | `Mission plan needs approval`; fixed explanatory copy | `/missions/{project_id}?tab=conversation&focus=plan-approval` | `open`, plus `approve_plan` only for the exact current actionable revision |
| `retry_required` | Mission run / `run:{run_id}` | current role is `owner|admin` | run status is `failed` | 8 | `Mission work stopped`; fixed safe retry copy | `/missions/{project_id}?tab=work&run={run_id}` | `open`; add `retry_work` only when the exact current Mission plan is approved, otherwise add `review_plan` |
| `workspace_action` | invitation/workspace record / its durable public event ID | the record's designated current human | the later W6 record remains pending | 12 | fixed action-specific product copy | server-selected safe workspace route | `open` plus the server-authorized action |

W2 projects the first seven rows from sources that exist in W2. For
`plan_approval`, authorization reads the exact retained plan-revision approval
record; `Mission.approved_contract_revision` is not its resolution source.
For event-backed assignment rows, the event snapshots the original recipient;
reassignment resolves the original row but does not erase it from All. For
manager-wide unassigned work, current `owner|admin` membership defines the
authorized audience while Task ownership defines only actionability. Losing
room membership always removes aggregate access regardless of history.

W2 reserves
the `workspace_action` union member but does not synthesize one before W6 adds
the durable source record. Ordinary agent progress, success, presence, raw
runtime events, and non-designated records are excluded.

The private read receipt is a separate CAS dimension. An absent
`(event_id,current_human_id)` receipt is exposed as `read=false, revision=0`.
`POST /workspace/attention/read {event_id,expected_revision}` rechecks current
room membership under the collaboration lock. Matching revision `0` creates
revision `1`; each later exact match updates `read_at` and increments once; a
stale value returns `409 {code:"revision_conflict",message:<fixed>}`. The
AttentionItem `revision` is this receipt revision. Marking read changes only
that current human's receipt: it never approves, verifies, assigns, retries,
resolves, or rewrites the source record, and never changes another human's
receipt.

`unread_count` and `actionable_count` are independent totals across the full
authorized aggregate before the selected filter and page limit. Attention is
ordered by `priority ASC, created_at DESC, id DESC`. Its opaque cursor is bound
to tenant, authenticated human, endpoint, and filter. Mission cursors are
bound to tenant, authenticated human, endpoint, and `state`; a cursor cannot
be reused across humans or query modes.

#### Search

```http
GET /workspace/search?q=...&types=message,work,file,crew&mission_id=...&cursor=...
```

#### Conversation

```http
GET  /projects/{id}/conversation?before=...&limit=50
POST /projects/{id}/conversation/messages
POST /projects/{id}/conversation/messages/{message_id}/replies
GET  /projects/{id}/conversation/messages/{message_id}/replies?before=...&limit=50
PUT  /projects/{id}/conversation/messages/{message_id}/reactions/{reaction}
DELETE /projects/{id}/conversation/messages/{message_id}/reactions/{reaction}
PUT  /projects/{id}/conversation/messages/{message_id}/saved
DELETE /projects/{id}/conversation/messages/{message_id}/saved
```

The conversation service may project existing comments/events. It must preserve one durable source of truth for work, approvals, outputs, and events.

### 5.2 Cursor and pagination contract

- Cursors are opaque, signed or integrity-protected.
- Page order is deterministic and documented per endpoint.
- Inserts after page one do not duplicate already-returned records.
- Invalid/expired cursor returns `400 cursor_invalid`, not an internal error.
- Maximum page size is 100 unless an endpoint specifies a lower cap.
- Public overviews are bounded; exact actionable records remain retrievable by ID.

### 5.3 Idempotency contract

Every create/send endpoint accepts `client_request_id`. Its normative record identity is always `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)`; the first four elements are server-derived from route/auth context, and a client supplies only `client_request_id` plus operation payload.

`POST /projects` bootstrap is the explicit pre-project exception: its reservation identity is `(tenant_id, authenticated_human_actor_id, operation="workspace_bootstrap", client_request_id)`, because `project_id` does not yet exist. The coordinator durably reserves `project_id` once, and all child operations immediately switch to the ordinary project-scoped identity.

- The same full identity and same canonical body returns the prior response.
- The same full identity with a different body returns `409 idempotency_mismatch`.
- Reusing the same request ID across tenant, project, authenticated human actor, or operation is isolated, never a collision or a replay lookup.
- Keys retain at least 24 hours; durable assignment/message keys retain for the object lifetime.

### 5.4 Event envelope

All customer-visible events use:

```json
{
  "id": "evt_...",
  "tenant_id": "...",
  "mission_id": "...",
  "type": "output.ready",
  "actor": { "id": "...", "kind": "agent" },
  "subject": { "id": "...", "kind": "output", "revision": 3 },
  "occurred_at": "...",
  "public_payload": {},
  "visibility": "mission",
  "dedupe_key": "..."
}
```

Public events cannot contain model IDs, session IDs, runtime names, local paths, credentials, token usage, raw tool arguments, or raw exceptions.

### 5.5 Realtime transport

V1 may retain polling, but the contract must support event-driven updates.

Recommended implementation:

- SSE endpoint per workspace and per Mission.
- Event IDs support `Last-Event-ID` resume.
- Heartbeat every 20 seconds.
- Client falls back to bounded polling after two reconnect failures.
- All UI updates are reconciled against durable reads; an SSE message is a wake-up signal, not authoritative state.
- Reconnect must never fabricate presence, work completion, or approval state.

### 5.6 Authorization and privacy

- Every aggregate endpoint applies tenant and Mission membership before fetching details.
- Room role and project role must not diverge silently; bootstrap guarantees a recovery-capable owner.
- Public serializers use explicit allowlists.
- Human email and account identifiers are returned only on authorized admin routes.
- Search, notification, and saved projections must be deleted or made inaccessible immediately after membership revocation.
- Private Codex state and trajectory export remain outside the customer conversation API.

### 5.7 Durable records and ownership

Implement the extensions below in the existing repository/service pattern. Do not store the same business state in both the Mission repository and a new UI-specific database.

| Record | Source of truth | Identity / concurrency | Retention |
| --- | --- | --- | --- |
| Conversation message | Collaboration repository | `message_id`; create key is the full `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)` identity; monotonically ordered by `(created_at, id)` | Mission lifetime, subject to workspace retention policy |
| Thread reply | Conversation message with `root_message_id` | same as message; root must exist in same Mission | Same as root; root deletion becomes tombstone |
| Reaction | Collaboration repository | unique `(message_id, actor_id, reaction)` | Deleted on explicit removal or message hard-deletion policy |
| `SavedReference` | Server-private `saved_references` subcollection in collaboration `conversation_state.json`, access-filtered to its human | unique `(tenant_id, human_id, object_kind, object_id)` | Until unsaved or account deletion; retain its tombstone when the target becomes unavailable, subject to workspace retention policy |
| Attention event | Typed domain event projection | source event ID + current human ID | Actionable until resolved; read receipt retained for configured history window |
| `AttentionReceipt` | Server-private `attention_receipts` subcollection in collaboration `conversation_state.json`, access-filtered to its human | CAS on `(event_id, human_id)` | Configured attention-history window or account deletion; never copied to the preferences store |
| External notification outbox | Operator external-delivery repository | unique typed-event delivery key per external channel/recipient; `delivery_id` is never an in-app identity | Terminal delivery metadata per operational retention policy |
| `WorkViewPreference` | Human-private preferences repository | CAS on `(human_id, view_scope)` | Until changed/account deletion; never stores saved references or attention receipts |
| `NotificationPreference` | Human-private preferences repository | One aggregate record per `(tenant_id, human_id)` with a single CAS revision; `event_selection` (`all_actionable`\|`mentions_and_decisions`\|`off`), channels, digest, and muted Mission IDs mutate atomically | Until changed/account deletion; delivery-only preferences never duplicate saved references or attention receipts |
| Search document | Derived index only | source object ID + source revision | Removed/reindexed after source change or authorization loss |

The source domain event is authoritative. V1 JSON persistence does not claim atomicity across the event file and notification outbox: a durable projector cursor derives missing idempotent outbox rows on replay. If PostgreSQL is introduced later it may use a database transaction and outbox table, but the public API and replay semantics remain identical.

Projection rebuilding must be possible from source records. Search indexes, workspace aggregates, unread counts, and board columns are derived data and may never become the only copy of a decision, message, work transition, output, or verification.

---

## 6. Frontend implementation specification

### 6.1 Application routing

Required routes:

```text
/missions
/missions/new
/missions/:missionId/conversation
/missions/:missionId/work
/missions/:missionId/files
/missions/:missionId/crew
/needs-you
/work
/settings/account
/settings/workspace
/settings/humans
/settings/connections
/settings/notifications
/settings/billing
```

Deep links add stable query parameters or fragments for message, work item, output, and crew member IDs. Reloading any authorized deep link returns to the same object.

### 6.2 State ownership

- Server state uses one query/cache layer with keys scoped by tenant and Mission.
- URL owns global page, Mission tab, filters, selected item, and deep-link target.
- Local component state owns unsent composer text, modal visibility, and transient hover/focus.
- Draft messages persist locally per Mission and human.
- Mutations use optimistic UI only when reversal is exact. Approvals, verification, retries, cancellation, invitations, and access changes wait for server confirmation.

### 6.3 Loading

- Initial shell uses a stable skeleton; navigation does not jump after load.
- Mission transition retains the prior shell and shows local content skeletons.
- Background refresh never replaces content with a full-page loader.
- Loading text says **Missions**, never Simulacra, CMUL8, Codex, project, room, or runtime.

### 6.4 Errors

Every public error has:

- user-facing title
- concise recovery instruction
- retry action when safe
- hidden/requestable support ID

Never show raw `Errno`, stack traces, local paths, database errors, response bodies, or internal IDs as the main message.

Mapping examples:

| Internal error | UI |
| --- | --- |
| permission denied / Errno 13 | Mission storage is temporarily unavailable. Your work is safe; retry in a moment. |
| revision conflict | This changed in another session. We loaded the latest version. |
| unapproved graph | Approve how the crew will work before starting. |
| missing runtime credential | Execution is not activated for this workspace. |
| agent timeout | The agent stopped at its work limit. Any recoverable output is ready for review. |

### 6.5 Accessibility

- All primary behavior is keyboard operable.
- Mention picker follows combobox/listbox ARIA patterns.
- Dialog focus is trapped and returns to the opener.
- New background events use polite live regions; urgent human decisions use one assertive announcement.
- Status never relies on color alone.
- Minimum touch target is 40 by 40 CSS pixels.
- Board view has a fully equivalent list view.
- Motion respects `prefers-reduced-motion`.
- Target WCAG 2.2 AA for customer-facing flows.

### 6.6 Responsive behavior

- Desktop: primary rail + Mission Crew rail + content.
- Tablet: primary rail collapses; Crew becomes a drawer.
- Mobile: bottom navigation for Missions, Needs you, Work, Settings; Mission tabs scroll horizontally or use a compact selector.
- Composer remains visible above the virtual keyboard.
- Opening a thread, output, or profile uses a full-screen detail route on mobile and a drawer on desktop.

### 6.7 Desired product effect

The interface must feel like a **quiet, inhabited Mission control room**: calm enough for serious work, alive enough that humans can sense agents and teammates carrying the outcome forward.

The emotional transformation is:

```text
From: fragmented work, uncertain ownership, invisible automation, approval anxiety
To: shared momentum, clear responsibility, visible proof, confident human control
```

The design should communicate five qualities before the human reads any explanatory copy:

1. **The Mission is the center.** The outcome is more visually important than the software chrome.
2. **The crew is present.** Humans and agents feel like teammates occupying the same place, not configuration records.
3. **Work is moving.** Current ownership and progress are visible without exposing runtime machinery.
4. **Human judgment has weight.** Decisions and verification are visually unmistakable but not alarmist.
5. **Evidence is durable.** Outputs feel reviewable and versioned, not like disposable chat attachments.

The compositional metaphor is **the briefing room**: one shared table for the conversation, a visible crew at the edge, a work ledger within reach, and a distinct place where decisions are signed.

This is not:

- a generic admin dashboard;
- a wall of equal cards;
- a developer IDE with infrastructure controls;
- a consumer chat application with decorative avatars;
- a glowing sci-fi agent cockpit;
- a direct copy of Raft's yellow, pink, pixel-art, or heavy-outline visual identity.

Borrow Raft's clarity of place, persistent membership, message-to-task interaction, and strong state visibility. Do not borrow its branding or its workspace/runtime complexity.

### 6.8 Signature composition

The product should remain identifiable in a blurred screenshot. The signature silhouette is:

```text
┌──────────┬─────────────────────────────────────────────────────┐
│ Global   │ Mission title / outcome / state / human actions     │
│ rail     ├────────────┬──────────────────────────────┬─────────┤
│          │ Crew rail  │ Primary Mission surface      │ Detail  │
│          │            │ Conversation / Work / Files  │ drawer  │
│          │            │                              │ optional│
│          │            ├──────────────────────────────┤         │
│          │            │ Contextual composer/actions  │         │
└──────────┴────────────┴──────────────────────────────┴─────────┘
```

#### Spatial rules

- The global rail is narrow and stable. It never becomes a second content sidebar.
- The Crew rail is visible only inside a Mission and is subordinate to the primary surface.
- Conversation owns the largest uninterrupted area.
- Detail drawers replace full-page context switches for agent profiles, thread replies, work details, and output review on desktop.
- Only one right-side drawer may be open at a time.
- Human-decision content can temporarily replace the right drawer with a wider review surface; it must not squeeze the Conversation below a usable width.
- Empty space belongs around the Mission outcome and between work phases. Dense information belongs inside work rows, message clusters, and evidence tables.
- Cards represent durable objects only: Mission, work item, approval, output, agent recommendation. Do not wrap headings, instructions, counts, and arbitrary sections in cards.
- Desktop content width is fluid. Conversation text has a readable maximum line length, while files and work tables may use the full content width.

#### Desktop dimensions

Use these as implementation defaults, not user preferences:

| Element | Default | Allowed range |
| --- | --- | --- |
| Global rail | 64 px | fixed |
| Crew rail | 232 px | 216–260 px |
| Mission header | 72 px | 64–80 px |
| Mission tab bar | 44 px | fixed |
| Detail drawer | 380 px | 340–460 px |
| Composer maximum width | 860 px | 720–960 px |
| Conversation reading width | 760 px | 680–840 px |

At widths below 1,020 px, close the persistent detail drawer before compressing Conversation. At widths below 760 px, Crew becomes a drawer or sheet.

### 6.9 Visual system

The first implementation should formalize the existing dark interface into semantic tokens rather than continue adding one-off colors to `styles.css`.

#### Color direction

Use a warm paper canvas, quiet porcelain and stone surfaces, a deep mineral navigation rail, restrained periwinkle for coordination, amber only for required human attention, green only for verified success, and red only for stopped/destructive states. Product work surfaces are light by default; dark chrome must not surround or overpower the Mission.

Initial token targets:

```css
:root {
  --mission-canvas: #f2f0e9;
  --mission-surface-1: #fbfaf6;
  --mission-surface-2: #e9e6dc;
  --mission-surface-3: #ddd9ce;
  --mission-border: #cfccc1;
  --mission-border-strong: #aaa79d;

  --mission-text: #20231e;
  --mission-text-secondary: #555b52;
  --mission-text-muted: #6c7169;

  --mission-accent: #465bb8;
  --mission-accent-strong: #34469b;
  --mission-accent-wash: rgba(70, 91, 184, 0.10);

  --mission-attention: #dfb85c;
  --mission-attention-wash: rgba(223, 184, 92, 0.10);
  --mission-verified: #69c892;
  --mission-verified-wash: rgba(105, 200, 146, 0.10);
  --mission-stopped: #df7777;
  --mission-stopped-wash: rgba(223, 119, 119, 0.10);
}
```

Rules:

- White is text, not the universal accent.
- Periwinkle means selected, assigned, linked, or actively coordinated—not success.
- Amber means a human decision is required or work is waiting—not generic emphasis.
- Green means verified/completed, never merely running.
- Red means destructive, failed, rejected, or stopped.
- Background gradients are prohibited in product work surfaces. A landing-page atmosphere may use one extremely low-contrast gradient.
- Every text/background pairing must pass WCAG 2.2 AA. Primary text should target 7:1 where practical.

#### Typography

- Continue using Geist for product UI and Geist Mono for identifiers, timestamps, versions, and compact evidence metadata.
- Reserve Instrument Serif for brand/landing statements and rare outcome moments. It must not appear in task tables, settings, or agent messages.
- Conversation body: 14–15 px, 1.55–1.65 line height.
- Work and file rows: 13 px primary, 11–12 px metadata.
- Mission title: 18–22 px semibold on the workplace; 28–36 px on the Mission home/detail hero.
- Eyebrows are 10–11 px, uppercase, 0.08–0.12 em tracking. Use them only for structural labels such as `CREW`, `NEEDS YOU`, or `OUTPUT`.
- Avoid using uppercase for buttons, long headings, or system explanations.
- Never use monospaced typography merely to make the product look technical.

#### Spacing and rhythm

- Base grid: 4 px; preferred component rhythm: 8 px.
- Dense rows use 8–12 px vertical padding.
- Durable-object cards use 16–20 px padding.
- Page sections use 24–32 px separation.
- Mission cards share equal height within a grid row and align title, state, crew, and footer baselines.
- Avoid large empty dashboard gaps caused by fixed grid columns. The Mission grid uses responsive auto-fill and cards stretch evenly.

#### Shape and depth

- Default radius: 8 px for controls, 10 px for rows/cards, 14 px for major dialogs.
- Pills are reserved for short status/filter values.
- Use borders and surface shifts before shadows.
- Shadows appear only on overlays, floating composer elements, and dragged objects.
- Avoid nested rounded rectangles. A card containing several smaller cards is a redesign signal.

### 6.10 Screen-level design changes

#### Missions home

Desired effect: a portfolio of outcomes in motion, not a gallery of generated artifact types.

- Replace type-first `APP / REPORT / SLIDES` emphasis with Mission state and current human need.
- Card hierarchy:
  1. Mission title
  2. concise outcome
  3. state sentence: `2 agents working`, `Waiting for your review`, `Scheduled for Monday`
  4. human/agent avatar stack
  5. last meaningful activity
- Every card in the same row has equal height.
- Card footer is pinned to the bottom so timestamps and crew never float at different vertical positions.
- Use a restrained state edge or 3 px marker; do not color the entire card by state.
- Add filters: Active, Needs you, Scheduled, Completed.
- The primary creation composer remains visually dominant above the list.
- Empty state gives one strong example and one action, not multiple panels of setup explanation.

#### Mission Conversation

Desired effect: humans and agents genuinely sharing a working room.

- Keep the Crew rail visible on desktop.
- Use aligned message clusters rather than every message becoming a large card.
- Human messages and agent messages share typography; differentiate through avatar, author label, and subtle surface treatment.
- Agent progress appears as compact timeline rows between substantive messages.
- Collapse repetitive progress updates into one expandable activity group.
- Assignment messages have a visible label, assignee row, and status—without resembling a system error.
- Human-decision cards use the amber semantic system, clearly state the decision and consequence, and present the primary action on the right.
- Output cards show file name, version, producer, evidence state, and one primary action.
- Composer is a grounded work surface with:
  - multiline input;
  - attachment control;
  - Message / Assign work mode;
  - recipient routing preview;
  - send action;
  - mention menu anchored to the caret/composer.
- Do not show a permanent toolbar of rarely used actions.
- The bottom of Conversation must remain reachable with keyboard and trackpad; no nested-scroll dead zones.

#### Needs you

Desired effect: a calm decision desk, not an alarm center.

- Default filter is Actionable.
- Each row answers: **What needs me? Why? What happens next?**
- Use one semantic icon and a narrow priority marker; avoid large alert cards.
- Group only when it aids action: Today, Earlier, Resolved—not by backend event type.
- Primary row action is visible on hover/focus and in the detail drawer; destructive/reject actions remain secondary.
- Unread uses a dot/weight change. Actionable uses state and copy. Do not conflate them.
- Empty state: `Nothing needs you right now.` followed by a subtle summary of active background work.

#### Workspace Work

Desired effect: a shared operational ledger.

- Default to grouped list; offer Board as a peer view, not a separate product.
- A compact status summary sits above filters, not as five oversized metric cards.
- Rows contain title, Mission, owner, state, updated time, and one next-action affordance.
- Board columns share width and scroll horizontally only below the desktop breakpoint.
- Dragging is optional and never the only way to change state.
- Filters live in a single collapsible bar and show active filter chips.
- Opening a work item preserves the list behind a drawer.

#### Mission Files

Desired effect: a durable evidence room.

- Sources and Outputs are persistent sub-sections or filters—not mixed in one undifferentiated grid.
- Default to a dense list for real filenames and metadata; offer a visual grid only for image/slide-heavy Missions.
- Preview occupies the right drawer on desktop and a routed full-screen view on mobile.
- Output versions use a vertical version rail or compact history list.
- Verification surface places evidence and preview before the decision buttons.
- `Verified` receives the green semantic treatment and named human verifier.
- Never show storage paths or opaque artifact references.

#### Mission Crew

Desired effect: a real team with clear responsibilities.

- Separate Agents and Humans with section headings, not separate pages.
- Row hierarchy: avatar, display name, job/role, presence/work state, current assignment.
- Clicking a member opens a profile drawer without leaving the Mission.
- Agent profile prioritizes job, current work, scope, guidance boundary, and recent outputs.
- Human profile prioritizes Mission role, presence, review responsibility, and recent decisions.
- Add Agent and Invite Human remain section-level actions.
- Do not group agents by machine, model, or provider.

#### Add Agent

Desired effect: hiring a specialist, not provisioning infrastructure.

- Use a two-stage form on smaller screens and one concise modal on desktop.
- Stage 1: choose a starter job or start blank.
- Stage 2: name, job, ownership statement, scope, human-guidance boundary.
- Example text is visually unmistakable placeholder text: lower contrast, italic only where readable, and removed immediately on input.
- Never prefill examples as real values.
- Use progressive disclosure for optional limits.
- Footer remains sticky while the form scrolls.
- Submit copy is **Add agent**, not Create runtime/agent instance.

#### Mission onboarding

Desired effect: reach useful teamwork quickly without an artificial product tour.

- After sign-in, ask for display name only if missing.
- Create the first workspace implicitly from organization/account context where possible.
- The first meaningful screen asks for the first Mission outcome.
- After Mission creation, show the proposed crew in Conversation and allow Add/Customize.
- Use contextual setup tasks embedded in the actual Mission: add source, add crew, approve how the crew works, assign first work.
- Do not require computer connection, provider selection, model selection, or a tutorial agent.
- The onboarding checklist disappears permanently once complete; it does not become another tab.

### 6.11 Component behavior and states

Every reusable interactive component must ship with these states where applicable:

- default
- hover
- keyboard focus-visible
- active/pressed
- selected
- loading
- empty
- disabled with reason
- success confirmation
- recoverable error
- stale/conflict
- permission denied

#### Required component primitives

- GlobalNavigation
- MissionHeader
- MissionTabs
- CrewRail and CrewMemberRow
- ConversationTimeline and MessageCluster
- AssignmentCard
- AgentProgressGroup
- HumanDecisionCard
- OutputCard
- Composer and MentionPicker
- AttentionRow
- WorkRow, WorkBoard, WorkDetailDrawer
- FileRow, FilePreview, OutputVersionHistory
- AgentProfileDrawer and HumanProfileDrawer
- AgentForm and HumanInviteForm
- EmptyState, InlineError, LoadingSkeleton, ConnectionBanner

The component library must not expose generic `Card` as the primary composition tool. Use domain components with defined information hierarchy.

#### Status copy

Use human-readable present-tense state:

| Internal state | Display |
| --- | --- |
| queued | Queued |
| preparing | Getting ready |
| running | Working |
| awaiting_approval | Waiting for a human |
| ready_for_review | Ready for review |
| succeeded | Completed |
| verified | Verified |
| failed | Stopped with an error |
| cancelled | Stopped |
| expired | Needs recovery |

Never animate an agent as working after durable state says it is no longer running.

### 6.12 Motion and feedback

- Motion explains state change; it is not ambient decoration.
- Default UI transition: 120–180 ms ease-out.
- Drawers: 180–220 ms with simultaneous opacity; no spring bounce.
- New Conversation item: subtle 120 ms fade/translate of at most 4 px.
- Agent working indicator: low-amplitude status pulse limited to the status dot; never animate whole rows or avatars continuously.
- Completion: one brief state-color transition; no confetti.
- Optimistic message send may place a local `Sending…` row. It becomes durable only after server reconciliation.
- Button loading preserves width and label context.
- Reduced-motion mode removes translations, pulses, and smooth scrolling.

### 6.13 Content design

- Prefer verbs and outcomes: `Review output`, `Assign work`, `Invite human`, `Add agent`, `Approve and continue`.
- Avoid technical nouns: execute, invocation, graph, runtime, artifact, transition, revision.
- Every empty state answers what the surface is for and gives at most one primary next action.
- Error messages state whether work is safe.
- Agent progress uses bounded product phrases such as `Reviewing sources`, `Preparing the exception list`, or `Checking the output`.
- Do not invent detailed progress when the backend has only a generic heartbeat.
- Humans are always called **humans**, never people/users, in product positioning and human-agent collaboration copy. `User` is acceptable only in technical documentation and account administration.
- Use sentence case throughout.

### 6.14 CSS and component refactor required

The current frontend has a large global `apps/console/src/styles.css` and a large `AgentShell.tsx`. Do not continue layering the new workplace entirely into those files.

Required structure:

```text
apps/console/src/design/
  tokens.css
  typography.css
  motion.css
  primitives.css

apps/console/src/features/workplace/
  shell/{contracts.ts,useWorkplaceQuery.ts,WorkplaceShell.tsx,workplace.css,WorkplaceShell.test.tsx}
  conversation/{ConversationTimeline.tsx,ConversationComposer.tsx,MentionPicker.tsx,ThreadDrawer.tsx,conversation.css,ConversationComposer.test.tsx,ConversationTimeline.test.tsx}
  crew/{CrewRail.tsx,crew.css}
  work/{WorkList.tsx,WorkList.test.tsx,work.css}
  files/{MissionFiles.tsx,FilePreview.tsx,files.css}
  attention/{AttentionInbox.tsx,attention.css}
  onboarding/{OnboardingChecklist.tsx,NotificationPreferences.tsx,onboarding.css,OnboardingChecklist.test.tsx,NotificationPreferences.test.tsx}
```

Migration rules:

- Move semantic tokens first; temporarily map old variables to new variables for compatibility.
- Extract one domain surface at a time from `AgentShell.tsx` without changing backend behavior in the same commit.
- Delete obsolete selectors after each migrated surface; do not leave two active visual systems.
- Feature styles may use semantic tokens only. Direct hex values are allowed only in the token source and data visualization palettes.
- Components use one icon library and consistent stroke weight.
- New product surfaces require Storybook or an equivalent isolated component harness with all states represented.

### 6.15 Design acceptance and screenshot gates

Every phase that changes customer UI produces screenshots at these viewports:

| Viewport | Size |
| --- | --- |
| Desktop wide | 1440 × 1000 |
| Desktop compact | 1180 × 820 |
| Tablet | 834 × 1112 |
| Mobile | 390 × 844 |

Required screenshot scenarios:

1. Missions home with 1, 3, and 12 Missions.
2. Mission Conversation empty, active multi-agent work, human decision, and completed output.
3. Mention picker with agents, humans, and `@Crew`.
4. Needs you with mixed actionable/unread/resolved items and empty state.
5. Work list and board at realistic density.
6. Files with sources, awaiting-verification output, verified version history, and preview.
7. Crew with multiple humans and agents, open agent profile, and Add Agent initial/validation-error/scrolling/submitting states.
8. Loading, disconnected, permission-denied, stale-conflict, recoverable-error, and mobile Conversation with keyboard-safe composer/open detail route.

There are exactly eight required scenarios. Each numbered scenario is one labeled composite visual-harness board, built from real production components, with every named sub-state above visible simultaneously in deterministic panels. The test-build-only client route is `/__visual__/scenario/{1..8}`. Each composite board is captured at all four viewports, so the visual-diff baseline contains exactly **32 screenshots** (8 boards × 4 viewports).

The composite baselines exercise component states and responsive composition. Separate named Playwright journey assertions exercise actual navigation, scroll ownership, overlays, keyboard routes, and interactions. Journey screenshots are retained as evidence artifacts only; they are not visual-diff baselines and are not counted in the 32.

Review criteria:

- The primary action and current human responsibility are identifiable within five seconds.
- At 20% blur, Conversation, Work, Files, and Needs you have different recognizable silhouettes.
- No page is dominated by equal-weight cards.
- Mission cards align evenly at all tested content lengths.
- Placeholder text cannot be mistaken for saved values.
- No horizontal page scroll at supported widths.
- Exactly one content region owns vertical scrolling on each major layout.
- All fixed/sticky areas leave the final row/message/action reachable.
- Realistic long names, titles, translated copy, and 200% browser zoom do not overlap or hide actions.
- Screenshots contain no runtime, provider, model, computer, MCP, local-path, or opaque-ID leakage.

Visual approval is a release gate, not a post-implementation polish task. A feature with correct API behavior but broken hierarchy, scroll, focus, or responsive layout is incomplete.

---

## 7. Delivery sequence

Each phase must be releasable and cannot depend on mock-only state.

### Phase 0 — contract cleanup and measurement

#### Work

- Freeze the terminology and route map in this document.
- Freeze the emotional direction, semantic visual tokens, signature shell composition, and anti-reference list from sections 6.7–6.9.
- Create the isolated component-state harness and four-viewport screenshot workflow before migrating product surfaces.
- Add public event envelope and error-code conventions.
- Add product analytics for Mission open, assignment, decision, output review, and completion.
- Add feature flags for the new shell, conversation projection, and workspace aggregates.
- Record baseline latency, error rate, and completion funnel.

#### Exit gate

- No public response added by later phases can expose runtime metadata.
- Token contrast, shell blur-test silhouette, and core component state review are approved.
- Existing Mission execution suite remains green.
- Product metrics distinguish human messages, assignments, decisions, and agent outcomes.

### Phase 1 — global shell and attention

#### Backend

- Workspace Mission summary endpoint.
- Workspace Work projection.
- Workspace Needs-you projection.
- Stable cursors and exact-item lookup.

#### Frontend

- Missions / Needs you / Work / Settings navigation.
- Workspace header utilities.
- Mission cards with consistent dimensions and meaningful states.
- Wire the existing `ActivityInbox` concept to real workspace attention data.

#### Exit gate

- A human with access to three Missions sees authorized counts and items only.
- Resolving an approval removes it from actionable Needs you without removing its history.
- A page refresh and browser back/forward preserve selection and filters.

### Phase 2 — shared Conversation

#### Backend

- Conversation projection and message send.
- Idempotent assignment/message transaction.
- Threads, reactions, and saved state.
- SSE wake-up stream or resilient polling contract.

#### Frontend

- Unified typed timeline.
- Thread drawer/detail.
- Reaction, save, link, and copy actions.
- Mention picker and routing preview.
- New-message position preservation.

#### Exit gate

- Two browsers see message, thread, reaction, and assignment changes without manual refresh.
- Resending after a network timeout creates one message and one assignment.
- An agent assignment always returns progress and completion to the same conversation.

### Phase 3 — Work and Files completion

#### Backend

- Complete discriminated Workspace Work projection.
- Secure file metadata/preview/download endpoints.
- Original-message and evidence links.

#### Frontend

- Work list/board with filters.
- Work detail drawer.
- Source/output/evidence file hierarchy.
- Preview, download, version history, and exact verification.

#### Exit gate

- Every Conversation assignment is discoverable in Mission Work and workspace Work.
- Every agent output is linked to its run, assignment, source message, evidence, and verification.
- Unverified staged code cannot appear in the public preview.

### Phase 4 — Crew, onboarding, and notifications

#### Backend

- Workspace invitation lifecycle.
- Notification outbox and delivery worker.
- Preferences and per-Mission mute.
- Account/profile fields required by the UI.

#### Frontend

- First-workspace and first-Mission onboarding.
- Crew directory/profile polish.
- Invitation states.
- In-app/browser/email notification settings.
- Connections page with customer vocabulary.

#### Exit gate

- A first-time human creates a Mission, adds an agent, invites a second human, and assigns useful work without documentation.
- Background completion reaches the designated human once through configured notification channels.

### Phase 5 — SaaS and mobile readiness

#### Work

- Usage and billing.
- Workspace lifecycle and support diagnostics.
- Mobile layouts and push behavior.
- Help, feedback, and release notes.
- Data retention and deletion UX.

#### Exit gate

- Self-serve signup-to-first-verified-output is production-supported.
- Workspace owner can understand limits, invite status, notification state, and billing without operator help.

---

## 8. Reliability test architecture

The test system must prove behavior, not merely component rendering.

### 8.1 Test layers

| Layer | Purpose | Required tooling |
| --- | --- | --- |
| Model/property tests | State machines, validation, retention, idempotency | pytest + property-based generation where valuable |
| Repository tests | Atomicity, locking, recovery, path safety | pytest with real filesystem and fault injection |
| Service tests | Authorization, transitions, projections, concurrency | pytest |
| API contract tests | Public shapes, errors, pagination, redaction | FastAPI test client + schema snapshots |
| Frontend unit tests | Reducers, routing, mention parsing, view models | Vitest |
| Component interaction tests | Keyboard, focus, loading, error, optimistic behavior | Testing Library |
| Browser E2E | Real user flows and two-browser collaboration | Playwright |
| Runtime integration | Real worker/queue/sandbox boundaries | Linux CI and container tests |
| Deployment smoke | Boot, readiness, worker consumption, persistence | Docker Compose / release environment |
| Visual regression | Layout stability at key viewports | Playwright screenshots |
| Accessibility | Automated rules plus keyboard scenarios | axe + Playwright |
| Load/soak | Queue, attention, search, SSE, retention | k6 or Locust + long-running worker tests |

### 8.2 Required invariant tests

These tests are release-blocking.

#### Authorization

- A user cannot discover a Mission, message, file, work item, output, or count after membership removal.
- A workspace admin without Mission membership receives no Mission payload unless an explicit recovery rule grants access.
- A member cannot add agents or approve the access plan.
- An output producer cannot verify their own output.
- A human cannot decide an approval unless designated by the approval policy.
- Search and attention totals do not leak unauthorized object counts.

#### Idempotency

- Duplicate Mission bootstrap with the same tenant-scoped pre-project reservation identity creates one Mission, room, graph, and immutable source adoption set.
- Duplicate message request with the same full idempotency identity creates one message.
- Duplicate assignment request with the same full idempotency identity creates one message and one run.
- Duplicate reaction request with the same full idempotency identity creates one reaction.
- Replayed automation occurrence creates no duplicate run.
- Retried external notification delivery uses its durable provider delivery ID; in-app attention remains one authoritative row per attention/event identity.

#### Concurrency

- Two workers cannot claim live work for the same Mission concurrently.
- Two humans claiming one task produce one winner and one conflict response.
- Approval with a stale run revision does not change state.
- Verification with a stale output version does not promote anything.
- A live `replace` queues a successor rather than state-cancelling the invocation.
- A live `merge` creates one follow-up rather than changing the bound prompt.

#### Persistence and recovery

- Crash before atomic replace leaves the previous valid state.
- Crash after outbox creation but before delivery is repaired by replay; the console eventually shows one delivery ID, while an unsupported external provider can receive a redelivery after handoff crash.
- Expired pre-invocation lease can be recovered safely.
- Expired post-invocation lease requires human recovery and never auto-replays side effects.
- Oversized retained evidence cannot make Mission discovery fail.
- Reconnect after missed SSE events reconciles to durable truth.

#### Security and privacy

- Secrets in input, model output, exceptions, query strings, URLs, nested objects, and PEM blocks never persist in public messages/events/search/notifications.
- Public payloads never include provider, model, session, usage, runtime, raw tools, environment, or local paths.
- Symlinks, traversal, descriptor swaps, and oversized files cannot escape allowed roots.
- Unverified code remains in attempt-unique staging and cannot change the canonical app or public preview.
- File preview uses safe content headers and cannot execute active content in the application origin.

#### Budget and cancellation

- Tool action N is allowed and N+1 is interrupted before completion.
- Partial writes from a stopped/failed turn are detected and exposed only as awaiting-verification candidates.
- Wall timeout covers startup and the entire turn.
- Stop terminates the complete process group, including resistant descendants.
- Cancel does not falsely report success and does not remove recoverable evidence.

### 8.3 Browser E2E journeys

### E2E-01: first verified output

1. New human signs up.
2. Creates a workspace and Mission.
3. Attaches a source.
4. Adds a recommended agent.
5. Approves how the crew will work.
6. Assigns with `@Agent`.
7. Browser acceptance uses the deterministic fixture runtime/fake harness; it proves the UI flow without a real worker or external provider.
8. Output appears in Conversation, Work, Files, and Needs you.
9. Human opens exact evidence and verifies.
10. Output moves to Verified and the Mission records the decision.

Assertions:

- No runtime fields appear in DOM or network payloads.
- One assignment creates one run.
- All four surfaces reference the same run/output IDs.
- Verified hash equals reviewed hash.

This browser E2E is not a real-worker claim. The separate Linux-runtime integration gate in Section 13 runs `test_real_mission_worker_assignment_reaches_awaiting_verification` through the production MissionWorker/Codex transport boundary with a deterministic local model transport and no external provider.

### E2E-02: two-human collaboration

1. Owner invites reviewer.
2. Reviewer joins in browser B.
3. Owner sends a message and assigns work.
4. Browser B receives update without reload.
5. Agent requests a checkpoint from reviewer.
6. Reviewer approves.
7. Owner sees the recorded decision.

Assertions:

- Display names are used, not raw user IDs.
- Presence converges but does not affect authorization.
- Only the designated human can decide.
- Read/unread state is private per human.

### E2E-03: multi-agent handoff

1. Mission has Researcher and Builder.
2. Human assigns `@Researcher @Builder` in that order.
3. Researcher completes a durable handoff.
4. Builder receives the handoff and sources.
5. Both agent contributions appear in Conversation.

Assertions:

- Execution order matches mention order.
- Builder cannot start before the first handoff settles.
- Final output records both contributing agents.

### E2E-04: network loss and idempotent resend

1. Intercept message response after server commit.
2. Client times out and retries.
3. Restore network.

Assertions:

- One human message.
- One assignment/run.
- Composer clears only after reconciliation.
- No duplicate notification.

### E2E-05: conflict

1. Two browsers open the same approval/output revision.
2. Browser A decides.
3. Browser B submits stale decision.

Assertions:

- Browser B receives a friendly conflict state.
- Latest decision is loaded.
- No second decision or promotion occurs.
- Browser B's review note remains copyable/recoverable.

### E2E-06: automation

1. Human creates a scheduled Mission.
2. Worker tick occurs across two replicas.
3. One run is created.
4. Result enters normal Conversation/Work/Files/Needs-you surfaces.

Assertions:

- One occurrence, one run.
- Disabled or unapproved automation is not consumed.
- Live-work concurrency policy behaves exactly as configured.

### E2E-07: staged code

1. Builder agent writes a code change.
2. Public preview is checked before verification.
3. Human opens exact staged file and verifies.
4. Promotion completes.

Assertions:

- Preview is unchanged before verification.
- Wrong hash and tampered staging are rejected.
- Exact verified bytes appear after promotion.

### E2E-08: accessibility and mobile

Complete Mission creation, agent addition, assignment, checkpoint decision, and output verification using keyboard only at desktop width and touch emulation at mobile width.

Assertions:

- Logical focus order.
- No trapped or lost focus.
- Dialog names and status announcements are correct.
- Composer is not hidden by virtual keyboard viewport behavior.

### 8.4 Contract tests

Maintain committed schemas/examples for:

- Mission summary
- Conversation message union
- Workspace Work union
- Attention item
- Crew member public profile
- File/output metadata
- Public error
- SSE event

For every schema:

- unknown internal fields in service objects must not appear publicly;
- frontend decoder rejects incompatible changes in CI;
- additive optional changes remain backward compatible;
- typed fixture snapshots contain no banned internal vocabulary.

### 8.5 Performance and scale gates

Initial V1 targets:

| Operation | Target |
| --- | --- |
| Application shell usable | p75 < 1.5 s on warm CDN connection |
| Mission summary page | p95 API < 400 ms for 100 Missions |
| Conversation initial page | p95 API < 350 ms for 50 messages |
| Message send acknowledgement | p95 < 500 ms, excluding agent execution |
| Workspace Work | p95 API < 500 ms for 10,000 retained source records |
| Needs-you page | p95 API < 400 ms |
| Search | p95 < 800 ms for V1 workspace limit |
| Realtime visible convergence | p95 < 2 s |
| Worker claim-to-start | p95 < 5 s when capacity is available |

Load tests must include 100 Missions, 20 humans, 50 agents, 10,000 messages, 5,000 work records, and 1,000 files in one test workspace without loading all state into a single response.

### 8.6 Soak tests

- 24-hour cron run with multiple worker replicas and injected restarts.
- 8-hour SSE reconnect test with proxy disconnects and missed events.
- Repeated message/assignment sends with 1% response loss.
- Retention-boundary test past event, approval, run, and trigger occurrence caps.
- File preview/download concurrency while verification and source ingestion occur.

### 8.7 Design reliability tests

Visual quality must be repeatable under real data, not dependent on a hand-picked demo workspace.

Automated tests must cover:

- Mission cards with titles of 12, 60, 120, and 240 characters.
- Human and agent names containing spaces, hyphens, non-Latin characters, and 80-character extremes.
- Zero, one, ten, and fifty crew members.
- Empty, short, long, Markdown, table, code, and attachment-heavy messages.
- A thread with zero, one, three, and one hundred replies.
- One and fifty simultaneous Needs-you items.
- Work boards with empty columns and columns containing one hundred rows.
- Filenames with long extensions, Unicode, duplicate visible names, and unavailable/tombstoned records.
- Browser zoom at 100%, 150%, and 200%.
- Reduced motion, high contrast/forced colors, and keyboard-only navigation.
- Slow API, reconnecting SSE, partial page errors, and stale mutation conflict.
- Virtualized lists preserving focus, deep-link position, and screen-reader order.

CSS/build checks must fail when:

- a new feature stylesheet introduces a direct color value outside the token layer;
- a customer-facing component uses an unapproved font family;
- a clickable icon lacks an accessible name;
- a modal/drawer lacks a focus return target;
- a page creates body-level horizontal overflow at a supported viewport;
- a sticky composer/header makes the final interactive row unreachable;
- screenshot fixtures expose banned internal terminology.

Visual regression thresholds must be low enough to catch alignment and overflow changes. Large intentional redesigns require reviewed baseline replacement; developers may not raise the global diff threshold to make a change pass.

---

## 9. Observability and support

Customer-facing product events and operator telemetry are separate.

### Product metrics

- signup to first Mission
- Mission to first agent
- Mission to first assignment
- assignment to first agent start
- agent start to human decision/output
- first verified output
- actionable-item age
- retry/cancel rate
- notification open-to-decision conversion

### Operator metrics

- API error rate by public error code
- projection lag
- SSE connection/reconnect count
- message idempotency replays/mismatches
- queue depth and oldest age
- worker lease expiry stage
- notification outbox backlog/dead letters
- search indexing lag
- preview/download denial rate
- secret-screen rejection count without rejected value content

### Support IDs

Public errors include an opaque support ID. Operators can resolve it to internal trace data. The support ID must not encode tenant, project, path, provider, or credential information.

---

## 10. Release gates

A phase cannot ship unless:

- Python tests pass.
- TypeScript typecheck passes.
- Production frontend build passes.
- API schema contract tests pass.
- Browser E2E for the changed journey passes.
- Two-browser collaboration tests pass when multiplayer behavior changes.
- Linux sandbox/runtime integration passes when execution behavior changes.
- Accessibility scan has no serious/critical violations on changed pages.
- Visual regression is reviewed for desktop, tablet, and mobile.
- No public payload contains a banned internal field.
- No new action bypasses Mission membership, revision checks, access-plan approval, or verification rules.
- Rollback preserves existing Mission evidence and conversation history.

The release candidate must also pass this manual product test:

> A person unfamiliar with the implementation creates a Mission, adds an agent, invites a human, assigns work, leaves the page, returns through a notification, reviews the exact output, and verifies it without being told what a runtime, graph, project room, MCP server, or computer is.

---

## 11. Definition of done by surface

| Surface | Done when |
| --- | --- |
| Missions | Consistent cards, useful states, pagination/filtering, deep links, no template data |
| Needs you | Every consequential human action appears once and disappears only when resolved |
| Work | All source work types project coherently with stable status and allowed actions |
| Conversation | Durable human/agent teamwork, threads, assignments, progress, outputs, reconnect |
| Files | Sources/outputs/evidence are secure, previewable, versioned, and provenance-linked |
| Crew | Multiple humans/agents, clear jobs and status, no runtime infrastructure |
| Search | Authorized exact navigation across all customer-visible information |
| Notifications | Durable projected outbox, preference-aware delivery, no visible duplicates |
| Onboarding | First useful verified output without documentation or operator intervention |
| Mobile | Core Mission loop works with touch and constrained viewport |

---

## 12. Implementation order for the next sprint

Start with these tasks, in order:

1. Create shared public TypeScript/Python contracts for Mission summary, Work item, Attention item, Conversation message, and public error.
2. Establish semantic design tokens, the signature shell grid, and the isolated component-state harness.
3. Implement workspace Mission summary and Needs-you aggregate endpoints with stable cursor tests.
4. Add the four-destination application shell and route state.
5. Wire the existing Activity Inbox UI to workspace Needs-you data.
6. Implement idempotent conversation message + assignment transaction.
7. Replace the mixed chat/event rendering with the typed Conversation union and domain components.
8. Add two-browser message/assignment/reconnect Playwright coverage and screenshot gates.
9. Implement threads and saved messages.
10. Complete workspace Work projection and board/list/filter persistence.
11. Add secure file preview/download and provenance links.

Do not start billing, marketplace-style connections, or extensive visual polish before tasks 1–7 prove the shared-workplace loop reliably.

### 12.1 Executable work packages

| ID | Deliverable | Backend responsibility | Frontend responsibility | Required proof | Depends on |
| --- | --- | --- | --- | --- | --- |
| WP-01 | Public contracts | Define discriminated schemas, error codes, cursor envelope, banned-field test helper | Add decoders/types and exhaustive rendering switches | Python/TypeScript contract fixtures round-trip; banned fields rejected | — |
| WP-D1 | Visual foundation | None | Semantic tokens, typography, motion, layout grid, responsive breakpoints, compatibility mapping from old variables | Contrast matrix, token lint, shell screenshots at four viewports | — |
| WP-D2 | Domain component harness | None | Isolated states for Conversation, Assignment, Decision, Output, Attention, Work, File, Crew, form, error and loading components | Every state in 6.11 rendered; keyboard/axe component tests | WP-D1, WP-01 |
| WP-02 | Workspace Mission summaries | Authorized bounded projection and cursor | Replace ad-hoc recent-project list with routed Mission list | 100-Mission pagination, membership-removal, stable-card E2E | WP-01 |
| WP-03 | Needs-you service | Aggregate typed actionable events and private read receipts | Global inbox, filters, badges, deep links | Old actionable item survives history pressure; read is not resolve | WP-01 |
| WP-04 | Application shell | No new business state | Four primary routes, header utilities, responsive navigation | URL/back/forward/reload and mobile navigation E2E | WP-02, WP-03 |
| WP-05 | Idempotent conversation send | Message service, request idempotency, typed public projection | Typed timeline and resilient send state | Lost-response retry creates one message | WP-01 |
| WP-06 | Recoverable assignment send | Journal-coordinate exactly-once public message/task/run outcome with stable assignee IDs | Mention picker, mode preview, setup gating | One message/one run; ordered multi-agent handoff E2E | WP-05 |
| WP-07 | Realtime reconciliation | Mission/workspace SSE wake-up streams and resume IDs | Cache invalidation, reconnect fallback, position preservation | Two-browser and missed-event recovery E2E | WP-03, WP-05 |
| WP-08 | Threads/reactions/saved | Thread root validation, reaction uniqueness, human-private saved refs | Thread detail, reply count, reaction and save actions | Two-browser threads; private saved state; keyboard tests | WP-05, WP-07 |
| WP-09 | Workspace Work | Discriminated projection with server-computed allowed actions | Board/list, filters, detail drawer, URL state | Projection parity and stale-mutation conflict E2E | WP-01, WP-06 |
| WP-10 | Secure Files | Opaque IDs, preview/download, provenance, immutable ranges | Sources/Outputs/Evidence hierarchy and viewers | Header/security/path tests and exact-verification E2E | WP-01, WP-05 |
| WP-11 | Invitations and profiles | Pending invitation lifecycle, display-name/profile API | Human onboarding, Crew/profile polish | Two-human join/revoke tests; no raw IDs in UI | WP-04, WP-08, WP-09, WP-10 |
| WP-12 | Notification delivery | Durable projected outbox, preferences, leased delivery, dedupe | Notification preferences and status | Crash/retry/duplicate transport tests | WP-03, WP-11 |
| WP-13 | Search | Authorized derived index and reindex/removal pipeline | Command palette, filters, grouped results | Revocation leakage, ranking stability, deep-link E2E | WP-05, WP-09, WP-10, WP-11 |
| WP-14 | Guided onboarding | Tenant/actor-scoped immutable source staging, pre-project bootstrap reservation, journal-backed recoverable graph-build durability/status, then project-scoped child orchestration and readiness state | First-workspace walkthrough and recovery states | Blob-first source publication/no-overwrite plus reserved-ID/graph-build-intent crash/retry/concurrent bootstrap proof; unfamiliar-user first-output acceptance session | WP-04, WP-06, WP-10, WP-11 |
| WP-15 | Mobile/accessibility | No special backend behavior beyond stable routes | Master/detail routing, touch layouts, focus/live-region polish | Keyboard/touch E2E and axe gates | WP-04–WP-14 |
| WP-D3 | Visual acceptance system | Provide deterministic seeded visual-test fixtures with no secret/internal data | Screenshot scenarios from 6.15, diff thresholds, review artifacts | Four-viewport baseline and scroll/zoom/long-copy tests in CI | WP-D2, WP-04–WP-15 |
| WP-16 | SaaS controls | Usage aggregation, limits, billing and retention APIs | Billing, limits, deletion, support information | Limit enforcement, billing webhook idempotency, delete/restore tests | Stable product loop |

### 12.2 Parallelization boundaries

Safe parallel work:

- WP-02 and WP-03 after WP-01.
- WP-D1 and WP-01 may run in parallel; WP-D2 starts when both stabilize.
- WP-05 backend projection and WP-04 frontend shell after their stated dependencies.
- WP-09 and WP-10 after shared contracts stabilize.
- WP-11/W6 depends on WP-04, WP-08, WP-09, and WP-10. In the implementation waves, W6 begins only after both W4 and W5 merge; W5 then transfers the collaboration paths. It never runs alongside its WP-08–WP-10 prerequisites.

Do not parallelize conflicting ownership:

- One owner defines the public union schemas in WP-01.
- One owner controls Conversation persistence/projection across WP-05, WP-06, and WP-08.
- One owner controls route and navigation state in WP-04 and WP-15.
- One owner controls typed-event/attention semantics across WP-03 and WP-12.
- One design-system owner controls WP-D1, domain component visual APIs in WP-D2, and screenshot acceptance in WP-D3.

Every work package lands with its tests and public contract changes in the same pull request. A frontend placeholder backed only by fixtures does not complete a package.

---

## 13. Implementation-grade TDD execution ledger

This section turns sections 1–12 into the binding implementation protocol. It is additive: where a row conflicts with an earlier normative product contract, the earlier contract wins and the row must be corrected before code begins.

### 13.1 Baseline, scope, and execution rules

The current worktree is dirty and contains user/other-agent changes, including Mission API, console, identity, and test edits. Implementation begins only from a **clean, reviewed baseline**: preserve, commit, or otherwise explicitly account for those changes first. Do not discard, reset, stash, or overwrite them to begin this work. The initial delivery owner records the selected baseline commit and the reviewed diff in the implementation PR description.

The first delivery scope is the vertical workplace loop: global **Missions / Needs you / Work / Settings**, then Mission **Conversation / Work / Files / Crew**, using the existing durable Mission and collaboration records. It deliberately excludes billing, customer-managed connections, provider/model/runtime configuration, raw runtime telemetry, and a generic replacement task database.

- Every new public response is an explicit allowlist and excludes model, provider, runtime, credential, host, path, raw tool, and raw exception fields.
- Ordinary unit tests freeze time, use in-process fakes/harnesses, and make no network or model call. Persistence, transaction, recovery, lock, and fault-injection tests instead use real `tmp_path` filesystems and subprocesses; they still make no network or model call.
- All `created_at` fixture values use UTC ISO-8601 strings and all IDs are fixed strings; UUID/random ID factories are injected or monkeypatched in tests.
- A branch cannot merge a RED test alone. Each work package merges test, minimal GREEN implementation, and REFACTOR cleanup together.
- A vertical slice must leave `uv run pytest -q` and `npm --prefix apps/console run build` runnable, even when an optional browser test lane is not yet enabled.

### 13.2 Repository-grounded ownership matrix

`EDIT` means the path exists at plan authoring time. `NEW` means it does not exist and must be created with the stated responsibility. No unmarked proposed path is permitted.

| Owner / lane | Files owned | Responsibility and boundary |
| --- | --- | --- |
| Contract and Mission domain (W1A) | EDIT `simulacra/missions/models.py`; EDIT `simulacra/missions/service.py`; EDIT `simulacra/missions/repository.py`; NEW `simulacra/missions/projections.py`; EDIT `tests/test_missions_v0.py`; NEW `tests/test_mission_projections.py` | Must complete GREEN and merge Mission models/repository/service plus projections first. It then transfers the three Mission persistence paths exclusively to W1C for the narrow `pending_commit` admission change, and transfers NEW `simulacra/missions/projections.py` plus NEW `tests/test_mission_projections.py` exclusively to W2. W1A/W1C and W1A/W2 never edit their respective transferred paths concurrently. |
| Collaboration conversation (W1–W5) | EDIT `simulacra/collaboration/models.py`; EDIT `simulacra/collaboration/service.py`; EDIT `simulacra/collaboration/repository.py`; NEW `simulacra/collaboration/conversation.py`; EDIT `tests/test_collaboration_domain.py`; NEW `tests/test_conversation_service.py` | Owns the single `conversation_state.json`, thread/reaction/saved/edit/delete records, and server-side mention validation through W5. It never coordinates a cross-store assignment. After W5 merges, all six listed paths—models, repository, service, conversation, `tests/test_collaboration_domain.py`, and `tests/test_conversation_service.py`—transfer exclusively to the W6 invitation/notification lane; no concurrent edits are permitted. |
| Workplace transaction coordinator (W1C) | NEW `simulacra/workplace/__init__.py`; NEW `simulacra/workplace/assignment_coordinator.py`; EDIT `simulacra/missions/models.py`; EDIT `simulacra/missions/service.py`; EDIT `simulacra/missions/repository.py`; EDIT `simulacra/missions/worker.py`; NEW `tests/test_assignment_coordinator.py`; EDIT `tests/test_mission_execution.py` | May begin Mission-path edits only after W1A GREEN/merge transfers Mission models/repository/service. Sole owner thereafter of journal enum PREPARED/COMMIT_DECIDED/STORES_DURABLE/COMPLETE/ABORTED, lock order, recovery, and MissionRun `pending_commit` admission gate. No other code acquires both store mutation paths for a conversation assignment. |
| HTTP, flags, and security envelope (W1D) | EDIT `apps/api/mission_routes.py`; EDIT `apps/api/cmul8_routes.py`; EDIT `apps/api/main.py`; NEW `apps/api/workplace_routes.py`; NEW `simulacra/workplace/config.py`; NEW `tests/test_workplace_api_routes.py`; NEW `tests/test_workplace_config.py`; EDIT `tests/test_mission_api_routes.py`; EDIT `tests/test_cmul8_api_routes.py` | Owns the stable workplace aggregator/mount/public-error/banned-field security envelope only, not `apps/api/security.py` or identity persistence. `workplace_routes.py` imports a declared static subrouter list and receives no later endpoint implementation edits. At W1D merge, transfer EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py` exclusively to W4 for preview-security work; W4 transfers both exclusively to W6B after its gate. Transfer EDIT `apps/api/cmul8_routes.py` plus EDIT `tests/test_cmul8_api_routes.py` exclusively to W6, except for W3's recorded narrow COMPLETE-assignment visibility/mutation correction; W3 returns both paths to W6 immediately after its gate. No other interim lane edits a transferred path. |
| Summary HTTP (W2) | NEW `apps/api/workplace_summary_routes.py`; NEW `tests/test_workplace_summary_routes.py`; NEW `simulacra/missions/projections.py` and NEW `tests/test_mission_projections.py` (only after W1A transfer) | Sole owner of `/missions` and `/workspace/attention` route implementations and their route tests, and of the transferred projection files while it adds summary projections. The W1D aggregator imports this subrouter without a later aggregator edit. At W2 merge it transfers `projections.py` and `test_mission_projections.py` exclusively to W4; W2/W4 never edit them concurrently. |
| Conversation HTTP and legacy-composer removal (W3) | NEW `apps/api/conversation_routes.py`; NEW `tests/test_conversation_api_routes.py`; EDIT `apps/console/src/components/AgentShell.tsx`; EDIT `apps/console/src/features/workplace/shell/{WorkplaceShell.tsx,WorkplaceShell.test.tsx,AppWorkplaceBoundary.test.tsx,workplace.css}`; EDIT `apps/console/src/App.tsx`; EDIT `apps/console/src/api.ts` (all EDIT paths only after the recorded W2 transfer); narrow correction EDIT `apps/api/cmul8_routes.py` plus EDIT `tests/test_cmul8_api_routes.py` | Sole owner of initial conversation GET/POST/PATCH/DELETE route implementations and route tests, the flag-on selected-Mission integration, shared authenticated conversation client functions, and removal of the legacy direct-run/composer submission from AgentShell while preserving flag-off builder chat. Its narrow cmul8 correction makes assignment-created tasks visible and mutable only after COMPLETE admission, preserves ordinary lifecycle changes, and leaves legacy tasks unchanged; those two paths return to W6 immediately after W3 GREEN. At W3 merge, transfer `conversation_routes.py`, `test_conversation_api_routes.py`, `App.tsx`, and `api.ts` exclusively to W5 for replies/reactions/saves, SSE feature wiring, and shared authenticated client additions. W5 then transfers `App.tsx` to W7 and `api.ts` to W6 after its gate; no owners edit a transferred path concurrently. The W1D aggregator imports this subrouter without a later aggregator edit. |
| Workplace shell and UI (W2) | EDIT `apps/console/src/App.tsx`; EDIT `apps/console/src/api.ts`; EDIT `apps/console/src/styles.css`; EDIT `apps/console/src/components/AgentShell.tsx`; EDIT `apps/console/src/components/CommandPalette.tsx`; EDIT `apps/console/src/components/Sidebar.tsx`; EDIT `apps/console/src/features/activity/ActivityInbox.tsx` (retire after W2); NEW `apps/console/src/features/workplace/shell/{contracts.ts,useWorkplaceQuery.ts,WorkplaceShell.tsx,workplace.css,WorkplaceShell.test.tsx,AppWorkplaceBoundary.test.tsx}` | Four global destinations, URL/query state, fetch/retry/cursor behavior, and no duplicate local business state. After W2 merge, transfer EDIT `apps/console/src/App.tsx`, EDIT `apps/console/src/api.ts`, EDIT `apps/console/src/components/AgentShell.tsx`, and the selected-Mission integration paths `WorkplaceShell.tsx`, `WorkplaceShell.test.tsx`, `AppWorkplaceBoundary.test.tsx`, and `workplace.css` exclusively to W3. W3 then transfers `App.tsx` and `api.ts` to W5; W5 transfers `App.tsx` to W7 and `api.ts` to W6 after its gate. NEW `useWorkplaceQuery.ts` transfers exclusively to W5 for SSE reconciliation. |
| Mission workspace UI | EDIT `apps/console/src/features/missions/MissionPod.tsx`; EDIT `apps/console/src/features/project-room/ProjectRoom.tsx`; EDIT `apps/console/src/features/project-room/ProjectRoomContainer.tsx`; EDIT `apps/console/src/features/project-room/TaskBoard.tsx`; EDIT `apps/console/src/features/team/TeamRoster.tsx`; NEW `apps/console/src/features/workplace/conversation/{contracts.ts,MissionConversationWorkspace.tsx,ConversationTimeline.tsx,ConversationComposer.tsx,MentionPicker.tsx,ThreadDrawer.tsx,conversation.css,ConversationComposer.test.tsx,ConversationTimeline.test.tsx}`; NEW `apps/console/src/features/workplace/crew/{CrewRail.tsx,crew.css}`; NEW `apps/console/src/features/workplace/work/{WorkList.tsx,WorkList.test.tsx,work.css}`; NEW `apps/console/src/features/workplace/files/{MissionFiles.tsx,files.css}`; NEW `apps/console/src/features/workplace/attention/{AttentionInbox.tsx,attention.css}` | Typed Mission Conversation / Work / Files / Crew views consume server contracts only. W3 owns the new conversation workspace/components through its gate. W4 exclusively owns the preview-specific `FilePreview.tsx` and `FilePreview.test.tsx` subpaths through its preview security gate. After W4 merges, transfer EDIT `apps/console/src/features/project-room/ProjectRoomContainer.tsx` and EDIT `apps/console/src/features/team/TeamRoster.tsx` exclusively to W6 for presence/invitation/member/profile integration; no W4/W6 overlap. |
| Work/File/Preference HTTP (W4) | NEW `apps/api/work_routes.py`; NEW `apps/api/file_routes.py`; NEW `apps/api/preview_routes.py`; NEW `tests/test_work_file_routes.py`; NEW `tests/test_preview_origin_routes.py`; NEW `simulacra/workplace/preferences.py`; NEW `apps/api/preference_routes.py`; EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py` (only after W1D transfer); NEW `simulacra/missions/projections.py` and NEW `tests/test_mission_projections.py` (only after W2 transfer); narrow EDIT `simulacra/missions/service.py` after W5 release transfer; EDIT `deploy/environment-contract.json`; EDIT `deploy/environment.py`; EDIT `apps/console/vite.config.ts` (only after W0 transfer); NEW `tests/test_workplace_preferences.py`; EDIT `apps/console/src/components/PreviewDrawer.tsx`; EDIT `apps/console/src/components/RightPanel.tsx`; NEW `apps/console/src/components/{PreviewDrawer.test.tsx,RightPanel.test.tsx}`; NEW `apps/console/src/features/workplace/files/{FilePreview.tsx,FilePreview.test.tsx}`; NEW `apps/console/e2e/preview-origin.spec.ts`; NEW `apps/console/e2e/global-setup.ts` and NEW `apps/console/playwright.config.ts` (only after W0 transfer) | Sole owner of work/file routes, descriptor-safe per-human preference persistence/current-human API, the transferred projection files after W2, the existing preview-route security correction, and the dedicated-preview-origin exchange/capability/sandbox implementation across legacy and new preview surfaces. After W5's release gate, W4 receives a narrow one-field ownership transfer in `simulacra/missions/service.py`: every run-produced artifact, successful or failed, must persist its exact public-safe run association so Work/File projections do not invent or duplicate provenance; W4 may not change execution, admission, verification, or lifecycle behavior there. W1D transfers `apps/api/main.py` and `tests/test_mission_api_routes.py` after its merge; W1D aggregator imports W4 subrouters without later edits. W0 first creates the minimal two-origin **frontend** harness, Playwright global setup/config, and Vite seam, then transfers those exact browser paths to W4 so its preview-origin browser gate is executable. W4 owns preview-origin environment validation and the preview-build `base: "./"` mode, then transfers environment, Vite, global-setup, and Playwright-config paths exclusively to W7 after its gate; W7 alone may activate the default-off flag or extend browser tooling. At W4 merge, transfer `main.py` and `tests/test_mission_api_routes.py` exclusively to W6B bootstrap; W6 consumes the stable preference contract and does not edit any W4-owned file. |
| SSE, conversation extensions, and reconciliation (W5) | NEW `apps/api/workplace_event_routes.py`; NEW `tests/test_workplace_event_routes.py`; NEW `apps/api/conversation_routes.py` and NEW `tests/test_conversation_api_routes.py` (only after W3 transfer); EDIT `apps/console/src/App.tsx` and EDIT `apps/console/src/api.ts` (only after W3 transfer); NEW `apps/console/src/features/workplace/shell/useWorkplaceQuery.ts` (only after W2 transfer); EDIT `apps/console/src/features/workplace/shell/{WorkplaceShell.tsx,WorkplaceShell.test.tsx}`; EDIT `apps/console/src/features/workplace/conversation/{MissionConversationWorkspace.tsx,ConversationTimeline.tsx,ConversationTimeline.test.tsx,conversation.css}`; NEW `apps/console/src/features/workplace/conversation/{ThreadDrawer.tsx,ThreadDrawer.test.tsx}` | Sole owner of `/workspace/events`, reply/reaction/save additions to the transferred conversation router/tests, durable-GET EventSource reconciliation, and the thread/save/reaction UI. SSE is a wake-up only: every event triggers authorized durable GET reconciliation, duplicate rows are removed by stable message ID, the reading position is preserved, EventSource is cancelled on unmount, and bounded polling begins only after two reconnect failures. The W1D aggregator imports its static subrouters without a later aggregator edit. At W5 GREEN, transfer `App.tsx` to W7, `api.ts` to W6, and all six collaboration paths listed in the W1–W5 lane to W6; no concurrent edits cross those transfers. |
| Visual foundation | NEW `apps/console/src/design/{tokens.css,typography.css,motion.css,primitives.css}`; NEW `apps/console/src/design/{tokens.test.ts,typography.test.ts,motion.test.ts,primitives.test.ts}` | Owns semantic token, typography, motion, and primitive CSS plus deterministic harness tests; it does not edit product component state. |
| Deterministic test assets | NEW `tests/fixtures/{mission_workplace.json,conversation_cases.json,assignment_transaction.json}`; NEW `apps/console/src/test/fixtures/workplace.ts`; NEW `apps/console/src/test/{fakeClock.ts,fakeEventSource.ts}` | Sole owner of every fixture/fake listed in 13.4; all are fixed-time, no-network, and reusable by other lanes without mutation. |
| Invitation, notification, onboarding, profile, and presence (W6 only) | EDIT `simulacra/collaboration/models.py`; EDIT `simulacra/collaboration/repository.py`; EDIT `simulacra/collaboration/service.py`; NEW `simulacra/collaboration/invitation_acceptance.py`; NEW `simulacra/collaboration/notifications.py`; EDIT `simulacra/collaboration/presence.py`; EDIT `simulacra/demo/identity.py`; EDIT `simulacra/demo/clerk_auth.py`; EDIT `simulacra/demo/db.py`; EDIT `simulacra/demo/pg_store.py`; EDIT `apps/api/security.py`; EDIT `tests/test_collaboration_domain.py`; EDIT `tests/test_identity.py`; EDIT `tests/test_db.py`; NEW `tests/test_invitation_acceptance.py`; NEW `tests/test_notification_outbox.py`; EDIT `simulacra/deploy_process.py`; EDIT `apps/api/cmul8_routes.py`; EDIT `tests/test_cmul8_api_routes.py`; EDIT `apps/console/src/api.ts`; EDIT `apps/console/src/features/project-room/ProjectRoomContainer.tsx`; EDIT `apps/console/src/features/team/TeamRoster.tsx`; EDIT `apps/console/src/components/GuestAuthGate.tsx`; EDIT `apps/console/src/components/LoginPage.tsx`; EDIT `apps/console/src/components/ProfileManageModal.tsx`; NEW `apps/console/src/features/workplace/crew/CrewRail.test.tsx`; NEW `apps/console/src/features/workplace/onboarding/{OnboardingChecklist.tsx,NotificationPreferences.tsx,onboarding.css,OnboardingChecklist.test.tsx,NotificationPreferences.test.tsx}` | Begins only after both W4 and W5 merge. W1D transfers `cmul8_routes.py` and `tests/test_cmul8_api_routes.py` at its merge; W2 transfers `apps/console/src/api.ts` after W2; W4 transfers `ProjectRoomContainer.tsx` and TeamRoster at its merge; W5 transfers models, repository, service, and EDIT `tests/test_collaboration_domain.py`. W4 also supplies the stable preference repository/API contract. It owns verified-email identity persistence, the pre-membership acceptance principal/security dependency, invitation/member removal, server-derived presence, external outbox projector/delivery, and component/service onboarding tests only. At W6 merge it transfers EDIT `simulacra/deploy_process.py` exclusively to W6B for the bounded bootstrap-recovery tick. |
| Workspace bootstrap coordinator (W6B) | NEW `simulacra/workplace/bootstrap_coordinator.py`; NEW `simulacra/workplace/source_staging.py`; NEW `tests/test_workspace_bootstrap_coordinator.py`; NEW `tests/test_source_staging.py`; NEW `tests/test_operation_graph_store.py`; EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py` (only after W4 transfer); EDIT `simulacra/demo/runs.py`; EDIT `simulacra/demo/operation_graph_builder.py`; EDIT `simulacra/operation_graph/store.py`; EDIT `tests/test_operation_graph_contract.py`; EDIT `simulacra/deploy_process.py` (only after W6 transfer) | Begins only after W6’s verified-principal identity foundation has merged and the recorded W4/W6 transfers are available. Sole owner of tenant-scoped pre-project bootstrap reservation, immutable staged and project-scoped source ingestion, reserved-ID child creation, journal-backed recoverable graph-build intent/result and exact graph-head finalization, bounded recovery tick, and the existing `POST /projects` public contract. It does not edit `simulacra/demo/plan.py`, `simulacra/demo/jobs.py`, room persistence, or invitation paths: `init_plan`/`start_job` remain read-only references because their live `JobRecord` is process memory. |
| Browser-test/tooling (W0/W7) | EDIT `apps/console/package.json`; EDIT `apps/console/package-lock.json`; EDIT `apps/console/vite.config.ts`; EDIT `apps/console/src/App.tsx` (only after W2 transfer); NEW `apps/console/vitest.config.ts`; NEW `apps/console/src/test/setup.ts`; NEW `apps/console/src/test/harness.test.tsx`; NEW `apps/console/e2e/harness-smoke.spec.ts`; NEW `apps/console/e2e/global-setup.ts`; NEW `apps/console/playwright.config.ts`; NEW `apps/console/src/features/workplace/visual/{VisualScenarioBoards.tsx,VisualScenarioBoards.test.tsx}`; NEW `apps/console/e2e/workplace-loop.spec.ts` | W0 owns only the deterministic DOM harness and minimal two-origin **frontend**/Playwright harness, configuration, Vite seam, and browser smoke. It owns no Python fixture runtime and makes no provider/network-denial claim. On W0 merge, Vite/global-setup/Playwright-config paths transfer exclusively to W4 for its preview-origin gate; on W4 merge they transfer exclusively to W7, which consumes/extends them for the test-build visual route. Repository inspection found no current Playwright config or visual component, so these paths are NEW. |
| Deterministic fixture runtime (W7) | NEW `tests/support/workplace_fixture_runtime.py`; NEW `tests/test_workplace_fixture_runtime.py` | W7 alone owns the Python fixture runtime and its external/provider-network denial test. It is not created, started, or claimed by W0 or W4. After W4 transfers the browser configuration, W7’s global setup may start this runtime alongside only the test Vite servers for W7 browser proof. |
| Runtime integration | NEW `tests/test_workplace_real_worker_integration.py` | Owns only the pinned Linux-runtime-image integration test. It consumes the public production MissionWorker/Codex transport boundary with deterministic local model transport and no external provider; it is separate from Playwright and never claims live-provider coverage. A worker defect revealed here is a separately owned follow-up after coordinator-lane transfer, never a concurrent W7R edit. |
| Release and rollout (W7 only) | NEW `docs/MISSIONS_WORKPLACE_ROLLOUT.md`; EDIT `deploy/environment-contract.json` and EDIT `deploy/environment.py` (only after W4 preview-origin-environment transfer); NEW `tests/test_workplace_rollout.py` | Sole owner after W4's recorded transfer of internal tenant allowlist activation, preview-origin deployment/readiness, rollout/rollback evidence, and release drill. No earlier wave activates flags. |

Files outside a lane are read-only to that lane. A cross-lane edit is either split into a small ownership PR or explicitly transferred in the PR description. Existing `ActivityInbox.tsx` may be retired only in Wave 2 after `AttentionInbox.tsx` is live and all imports have moved; it must not remain a competing attention surface.

Ownership clarification for W5: after W3 GREEN, `ConversationComposer.tsx` and `ConversationComposer.test.tsx` transfer to W5 for the narrow centralized access-loss correction across message and assignment submission. This correction shares W5's selected-Mission access state; no W3 writer remains active.

Ownership clarification for W6: after W5 GREEN, its collaboration lane owns all six transferred paths, including `simulacra/collaboration/conversation.py` and `tests/test_conversation_service.py`, even where the W6 row abbreviates that set as the collaboration paths. W6 may edit those two paths only for member-removal/access-loss behavior and matching regression coverage.

Ownership clarification for W4 frontend integration: after W5 GREEN, `apps/console/src/api.ts`, `apps/console/src/features/workplace/shell/WorkplaceShell.tsx`, `WorkplaceShell.test.tsx`, and `workplace.css`, plus `apps/console/src/features/workplace/conversation/MissionConversationWorkspace.tsx` and `conversation.css`, transfer exclusively to W4. W4 uses those seams only to mount the global cross-Mission Work view, the selected-Mission Work and Files tabs, their authenticated clients, and the shared tab/access-loss presentation. W4 does not alter conversation persistence or composer behavior. At W4 GREEN, `api.ts` transfers exclusively to W6 and the shell/conversation integration paths transfer exclusively to W7; no W5, W6, or W7 writer edits them while W4 is active.

Preview revision clarification for W4: the promoted preview revision is the deterministic digest of the current verified code-deliverable manifest, ordered by fixed target path and containing each asset's verified hash and version. The manifest must include a verified index beneath the fixed promoted app root. An entry is eligible only when its durable verification evidence binds the deliverable's exact staged artifact reference to that canonical promoted target and the served bytes match the verified hash; an arbitrary `intended_target` string alone is never authority. Exchange and asset authorization bind to that digest, and every asset read revalidates current membership, capability scope, manifest membership, and exact bytes/hash. Directory presence, staged output, or an unverified `app/dist` tree never constitutes promotion.

### 13.3 Data, API, and compatibility contract

#### Data changes

Add the following versioned records to the existing JSON repository conventions, not to a second database:

| Record | Canonical fields | Storage / compatibility | Rollback |
| --- | --- | --- | --- |
| `ConversationState` | `messages`, `message_audits`, `reactions`, `saved_references`, `idempotency`, `attention_receipts`, `schema_version` | One NEW crash-atomic `conversation_state.json` under the collaboration repository, written by one fsync + atomic replace while holding the collaboration lock. Existing project comments are projected as legacy `human_message` rows; new writes use this state. | A pre-replace crash leaves neither edit/delete nor its audit; a post-replace crash leaves both. Feature flag off restores old rendering without deleting state. |
| `ConversationMessage` | `id`, `tenant_id`, `project_id`, `author`, `kind`, `body`, `created_at`, `revision`, `root_message_id`, `source_message_id`, `links`, `deleted_at` | An element of `conversation_state.json`; links are immutable across tombstone. | Never delete linked work/evidence when tombstoning. |
| `ConversationMessageAudit` | `id`, `message_id`, `operation` (`edit`\|`delete`), `actor_id`, `client_request_id`, `prior_revision`, `prior_body`, `resulting_revision`, `occurred_at` | Append to `message_audits` in the same `conversation_state.json` replacement as the edited body or tombstone. | Retained with tombstone and unavailable to unauthorized members. |
| `IdempotencyRecord` | `operation`, `authenticated_human_actor_id`, `client_request_id`, `canonical_body_hash`, `response_ref`, `created_at` | An `idempotency` element of crash-atomic `conversation_state.json`, keyed by `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)`. Retain message/assignment keys for object life and other keys for at least 24 hours. The human actor is server-derived and never accepted from a client body. | One state replacement makes the mutation and replay record durable together. |
| `AttentionReceipt` | `event_id`, `human_id`, `read_at`, `revision` | Private `attention_receipts` element in `conversation_state.json`; source events remain Mission/collaboration events. | Hide read state only; source action remains authoritative. |
| `WorkViewPreference` | `scope`, `view` (`list`\|`board`), `filters`, `revision`, `updated_at` | NEW `JsonWorkplacePreferenceRepository` in `simulacra/workplace/preferences.py` owns one descriptor-safe, crash-atomic `preferences_state.json` at `RUNS_DIR/.workplace-control/preferences/{tenant_id}/{human_id}/state.json`. It contains only this human's `work_view_preferences` and `notification_preference`; it never contains saved references or attention receipts. | Missing preference state reads as defaults. A flag-off UI may ignore it but never deletes it. |
| `NotificationPreference` | `event_selection` (`all_actionable`\|`mentions_and_decisions`\|`off`), `channels`, `digest`, `muted_mission_ids`, `revision`, `updated_at` | One aggregate record per `(tenant_id, human_id)` in the same per-tenant/per-human `preferences_state.json`; its single `revision` CAS atomically updates event selection, channels, digest, and muted Mission IDs. All scope IDs and every ancestor/leaf are validated descriptor-safe non-symlinks. Server validates the enum, channels, filter keys, and current Mission memberships for mute IDs. | A mute changes delivery selection only; it never deletes or suppresses an attention event. |
| `AssignmentTransaction` | `transaction_id`, `authenticated_human_actor_id`, `operation` (`conversation_assignment`), `client_request_id`, `canonical_request_hash`, `graph_revision`, `reserved_message_id`, `reserved_task_id`, `reserved_run_id`, `intended_payloads`, `state` (`PREPARED`\|`COMMIT_DECIDED`\|`STORES_DURABLE`\|`COMPLETE`\|`ABORTED`), `created_at`, `updated_at` | NEW coordinator journal under `RUNS_DIR/.workplace-control/{tenant_id}/{project_id}/assignment-transactions/{authenticated_human_actor_id}/{operation}/{client_request_id}.json`; its identity is `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)`. Scope IDs are validated and all ancestors/leaf are descriptor-safe, non-symlink directories/files. `pending_commit` and `queued` are MissionRun statuses, never journal states. | No journal is removed until all referenced records are durable and the completed-retention interval expires. |
| `StagedSource` | `source_ref`, `tenant_id`, `authenticated_human_actor_id`, `operation`, `client_request_id`, `canonical_content_sha256`, `normalized_filename`, `media_type`, `blob_ref`, `state`, `created_at` | NEW immutable descriptor-safe staging record at `RUNS_DIR/.workplace-control/source-staging/{tenant_id}/{authenticated_human_actor_id}/workspace_source_stage/{client_request_id}.json`, with bytes in the same controlled root. It is created before project reservation and is linked by immutable `source_ref`, never a client path. | Exact replay with the same hash/metadata returns the record; any body/hash mismatch is `409 idempotency_mismatch`; records are retained through bootstrap completion. |
| `WorkspaceBootstrapTransaction` | `transaction_id`, `tenant_id`, `authenticated_human_actor_id`, `operation` (`workspace_bootstrap`), `client_request_id`, `canonical_request_hash`, `reserved_project_id`, `staged_source_refs`, `graph_build_intent`, `graph_result_revision`, `state` (`PREPARED`\|`COMMIT_DECIDED`\|`STORES_DURABLE`\|`COMPLETE`\|`ABORTED`), `intended_project_mission_room_graph_payloads`, `created_at`, `updated_at` | NEW descriptor-safe journal at `RUNS_DIR/.workplace-control/bootstrap-transactions/{tenant_id}/{authenticated_human_actor_id}/workspace_bootstrap/{client_request_id}.json`. `graph_build_intent` is a durable canonical hash/input marker and `graph_result_revision` is set only after the graph store has a durable revision/head. This is the only pre-project idempotency identity; child writes use `reserved_project_id` and the standard project-scoped identity. | Retain through normal bootstrap idempotency retention; never allocate a second project ID for an exact replay. |

All new readers treat a missing collection as an empty collection. Serialization always writes `schema_version: 1`. No destructive migration occurs in Wave 1. A later compaction/migration is permitted only after a release has verified that the legacy projection and new collection produce equivalent message counts and deep links for a sampled Mission set. It must be an expand/contract migration with backup and the NEW planned `docs/MISSIONS_WORKPLACE_ROLLOUT.md` rollback runbook; rolling back code must continue to read the expanded shape.

For every `conversation_state.json` mutation, use one durability algorithm: create a full temporary state file in the same directory; write it completely; flush and `fsync` the temporary-file descriptor; atomically call `os.replace`; then `fsync` the parent directory. Crash tests inject failures before/after the write, temp `fsync`, replace, and parent-directory `fsync`; before replacement leaves neither change nor audit, after replacement leaves both.

`JsonWorkplacePreferenceRepository` uses the same one-file durability algorithm for `preferences_state.json`: create the full temporary state beside the target; write, flush, and `fsync` its file descriptor; atomically `os.replace` it; then `fsync` the parent directory. A crash before replacement leaves the prior complete preference state; a crash after replacement leaves the complete new state. Repository writes use CAS on the relevant record revision and never accept a client-supplied human ID.

#### Recoverable cross-store assignment coordinator

Conversation assignment crosses the collaboration and Mission repositories and therefore must not claim filesystem atomicity. NEW `simulacra/workplace/assignment_coordinator.py` is the only writer for this flow. It obtains locks in this fixed order: **per-project coordinator flock → graph approval lock for validation/pinning → collaboration repository mutation → Mission repository mutation**. No caller may hold collaboration and Mission mutation locks in an opposing order. The coordinator journal path is created under the common `RUNS_DIR/.workplace-control` root using `openat`/descriptor-safe checks; it validates tenant/project/request IDs and rejects symlink, directory, and nonregular journal targets.

1. Recover the request journal before accepting a retry. The server derives `actor_id` from current authentication and resolves only `(tenant_id, project_id, authenticated_human_actor_id, operation="conversation_assignment", client_request_id)`. The same full identity and canonical body hash returns the previously committed public result; a hash mismatch returns `409 idempotency_mismatch`. The same request ID from another tenant, project, human, or operation is a separate identity, never a collision; client bodies never carry actor ID.
2. Reserve deterministic message/task/run IDs; validate and pin the approved graph revision; write and fsync a durable `PREPARED` intent containing the identity fields (`actor_id`, `operation`, `client_request_id`), IDs, canonical request hash, graph revision, intended message/task/run payloads, and `transaction_id`.
3. Create idempotent collaboration records tagged `transaction_id`; they are excluded from public conversation/work projections while the transaction is not committed. Fsync the collaboration state.
4. Create the Mission run tagged `transaction_id` in non-claimable `pending_commit` state. Fsync the Mission state. A `pending_commit` run is never available to worker claim/dispatch.
5. Write and fsync the irreversible `COMMIT_DECIDED` journal marker. This marker solely makes the decision irreversible.
6. Confirm both stores are durable and write/fsync `STORES_DURABLE`; activate the Mission run from `pending_commit` to `queued`. The worker still rejects every tagged run unless the atomically replaced journal, read under the coordinator project lock, is exactly `COMPLETE`.
7. Write/fsync `COMPLETE`. Public Conversation/Work projections and Mission worker claim both require journal state exactly `COMPLETE`, checked under the same coordinator project lock. A crash after `queued` but before `COMPLETE` keeps records hidden and the run non-claimable; recovery writes `COMPLETE`. A crash after `COMPLETE` makes both public/claimable. Retain the journal until all references are durable and the idempotency retention condition is satisfied.

Request-path recovery runs before every message/assignment submission and worker-start recovery scans every incomplete journal before any claim. Recovery verifies the persisted tenant/project/authenticated-human-actor/operation/request identity before replaying; an authenticated retry with a different tenant, project, human actor, or operation cannot open another journal. Before the irreversible decision marker, recovery keeps tagged records invisible/non-claimable and deterministically revalidates the pinned graph and canonical request: a valid unchanged request resumes; an invalid/mismatched graph writes/fsyncs `ABORTED` and leaves tagged records permanently hidden/non-claimable. The same full identity then returns stable `transaction_aborted`; a changed intent requires a new `client_request_id`. After the irreversible decision, recovery completes without re-deciding and never rolls back. A client timeout/retry invokes this protocol. Recovery/cleanup must not delete a journal until its referenced records and decision state have been inspected under the coordinator lock.

#### Recoverable workspace bootstrap coordinator

NEW `simulacra/workplace/bootstrap_coordinator.py` is the sole orchestrator behind the existing `POST /projects`. It obtains **tenant bootstrap coordinator flock → project-root reservation lock → collaboration room lock → graph initialization lock**. From the authenticated tenant/human and `client_request_id`, it computes the tenant-scoped bootstrap identity, validates the ordered immutable `staged_source_refs`, reserves one deterministic `project_id`, writes/fsyncs `PREPARED`, and makes the reservation durable before any child creation. It then calls EDIT `simulacra/demo/runs.py` with the reserved ID to create the project and Mission, atomically adopts the staged immutable blobs into that project without a user-controlled path or overwrite, and creates/recovers the owner room.

The current `simulacra/demo/plan.py:init_plan` delegates graph creation through `simulacra/demo/jobs.py:start_job`; its `JobRecord` and active-job map are process memory, so an enqueued or completed-looking job is not restart-safe bootstrap evidence. W6B does not call or edit either seam. Before graph creation it writes/fsyncs `graph_build_intent` into the bootstrap journal, including the reserved project, current owner actor, canonical graph-input hash, and expected tenant/project scope. Under the graph initialization lock it calls a W6B-owned recoverable entry point added to EDIT `simulacra/demo/operation_graph_builder.py`. That entry point creates/validates the immutable revision and calls NEW locked `OperationGraphStore.finalize_exact_revision_head(tenant_id, project_id, revision_hash, canonical_graph_hash)`, owned by EDIT `simulacra/operation_graph/store.py`.

`finalize_exact_revision_head` is idempotent: under the store lock it reads the immutable revision bytes and validates exact tenant/project and canonical graph hash. If the current head is absent, it writes that exact head through a temporary file, flush, file `fsync`, atomic replace, and parent-directory `fsync`. If the current head already names that valid target revision, it returns it. If the current head names any different valid revision, or any revision bytes/scope/hash disagree, it returns a stable conflict: it never overwrites, rolls back, or duplicates a head/revision. A crash after immutable revision creation but before head publication leaves the revision recoverable but non-final; restart/recovery calls finalize and writes the formerly absent head exactly once. The coordinator sets `graph_result_revision` only after this finalizer succeeds; it never treats `create_revision` alone as durable bootstrap completion and never consults an ephemeral `JobRecord`.

Only after the durable graph revision exists in `approved` or `ready_for_approval` state does the coordinator write `graph_result_revision` and then `COMMIT_DECIDED`; a builder failure or scope/input mismatch before that decision writes `ABORTED`. It then verifies project, Mission, room, adopted source refs, and exact graph revision/head, writes/fsyncs `STORES_DURABLE`, and only then writes/fsyncs `COMPLETE`. The public bootstrap response is `202` with `provisioning` for every nonterminal recoverable state and `200` only at `COMPLETE`; it never advertises a ready Mission because a background job was merely enqueued. A process restart sees the durable intent/result in the journal, not a lost job, and the same recovery path resumes it.

`GET /projects/bootstrap/{transaction_id}` is current-auth only: the coordinator looks up the opaque transaction only beneath the caller's tenant/actor root; a different tenant or actor and a missing ID both return `404 bootstrap_unavailable`. It returns `202 {transaction_id, status, project_id, provisioning, retry_after_seconds}` for `PREPARED`, `COMMIT_DECIDED`, or `STORES_DURABLE`; `200` with the ordinary public bootstrap payload and `status:"COMPLETE"` for `COMPLETE`; and `409 {code:"bootstrap_aborted"}` for `ABORTED`. It does not expose a general tenant lookup. The W6B-owned bounded `bootstrap_recovery_tick` in transferred EDIT `simulacra/deploy_process.py` runs at process start and once per configured scheduler interval, takes the tenant coordinator lock, processes at most 100 incomplete journals per tick in lexicographic journal-path order, and invokes the same idempotent recovery method as request-path lookup. It owns no invitation/projector work. A request to `POST /projects` and `GET /projects/bootstrap/{transaction_id}` also runs recovery for that caller's journal before responding.

Before `COMMIT_DECIDED`, recovery deterministically revalidates the canonical request, immutable source refs/content hashes, durable graph-build intent/result, and exact graph revision/head and completes or writes `ABORTED`; after it, recovery completes without rollback. `tmp_path` fault injection covers staged-source blob/record boundaries, reservation write/fsync/replace, project/Mission write, source adoption, room write, graph-build intent write, graph revision/head durability, and every journal boundary; two-process tests prove one reserved project and one child set under concurrent retries.

Pre-creation bytes are never embedded in `POST /projects`. The UI first uses `POST /workspace/bootstrap/sources` as `multipart/form-data` with one `file` and form field `client_request_id`; current tenant and authenticated human are server-derived. The operation is `workspace_source_stage`; canonical body identity includes normalized filename, media type, and SHA-256 of bytes. Its response is `{source_ref, sha256, filename, media_type}`. The same tenant/actor/operation/request ID and same canonical content returns the original record; a different body/hash returns `409 idempotency_mismatch`; a conflicting target or existing immutable blob is never overwritten. `POST /projects` accepts only `staged_source_refs: string[]` and the UI supplies those immutable refs in its canonical bootstrap request. After reservation, the existing project upload seam is `POST /projects/{project_id}/upload` as `multipart/form-data` with `files[]`, form field `client_request_id`, and optional `reingest`; operation is `project_source_upload`. It records server-derived full project identity, normalized filename/media type/content SHA-256 and opaque source ID. Same identity/content replays metadata; a changed body/hash returns `409 idempotency_mismatch`; no target overwrite is allowed.

Source stage does not claim two-file atomicity. It first writes each immutable content-addressed blob to a same-directory temporary file, flushes and file-`fsync`s it, atomically replaces the final blob path, and `fsync`s that directory. Only after reopening and hash-verifying the final regular non-symlink blob does it publish the `StagedSource` record with its own temporary-file write, flush, file `fsync`, `os.replace`, and parent-directory `fsync`. The record replacement is the sole public publication boundary: a crash before it can leave an orphan blob that is invisible to all reads; it can never publish a record pointing to an absent/unverified blob. The W6B recovery tick and bounded descriptor-safe source GC remove an unreferenced orphan only after it is not named by any live stage record or incomplete bootstrap journal; they never remove blobs referenced by an existing record. Project adoption/upload use the same blob-first then metadata-publication sequence and no client-controlled overwrite.

#### Exact endpoint signatures

All endpoints use current auth dependencies, tenant scoping, existing `project_id` as the public Mission ID, and standard error body `{ "code": string, "message": string }`; bodies are JSON except the two explicitly marked multipart source-upload endpoints. `limit` defaults to 50 and is constrained to 1–100. Cursor order is encoded, integrity-protected `(sort_key, id)` and an invalid cursor returns `400 {"code":"cursor_invalid"}`.

| Endpoint | Request | Success response / deterministic order |
| --- | --- | --- |
| `POST /workspace/bootstrap/sources` | `multipart/form-data`: one `file`, `client_request_id`; tenant/human are current auth only | `{source_ref, sha256, filename, media_type}`. Operation is `workspace_source_stage`; same tenant/actor/request/content replays; hash or metadata mismatch is `409 idempotency_mismatch`; no blob/path overwrite. |
| `POST /projects` | `{client_request_id, prompt, goal, design_brief, artifact_kind, staged_source_refs: string[]}`; tenant is current authenticated tenant, never a client body field | `202 {transaction_id, status, project_id, provisioning, retry_after_seconds}` until durable completion, then `{project, readiness, recommended_crew, permissions, workspace_state, provisioning, transaction_id, status:"COMPLETE"}`. Operation is `workspace_bootstrap`; the tenant-scoped bootstrap reservation replays the same `project.id`, while same identity/different canonical request is `409 idempotency_mismatch`. |
| `GET /projects/bootstrap/{transaction_id}` | none; current authenticated tenant and actor only | `202 {transaction_id, status, project_id, provisioning, retry_after_seconds}` while recoverable; `200` ordinary public bootstrap payload at `COMPLETE`; `409 bootstrap_aborted` at `ABORTED`; missing/other actor/other tenant is `404 bootstrap_unavailable`. |
| `GET /missions?state={active\|all}&cursor=&limit=` | none | `{items: MissionSummary[], next_cursor: string\|null}` ordered `updated_at DESC, id DESC`. |
| `GET /workspace/attention?filter={actionable\|all}&cursor=&limit=` | none | `{items: AttentionItem[], next_cursor, unread_count, actionable_count}` ordered `priority ASC, created_at DESC, id DESC`. |
| `POST /workspace/attention/read` | `{event_id, expected_revision}` | `{item: AttentionItem}`; marks only this actor's receipt and never resolves source work. |
| `GET /workspace/preferences` | none | `{work_view_preferences: WorkViewPreference[], notification_preference: NotificationPreference}` for the current authenticated human only. |
| `PUT /workspace/preferences/work-view` | `{expected_revision, scope, view, filters}` | `{work_view_preference: WorkViewPreference}`; server allowlists `view` and filter keys; stale CAS is `409 revision_conflict`. |
| `PUT /workspace/preferences/notifications` | `{expected_revision, event_selection, channels, digest, muted_mission_ids}` | `{notification_preference: NotificationPreference}`; current-human only; server allowlists the event-selection enum and channels and rejects a mute ID without current Mission membership; stale CAS is `409 revision_conflict`. |
| `GET /workspace/work?bucket=&mission_id=&assignee_id=&cursor=&limit=` | none | `{items: WorkItem[], next_cursor}` ordered `updated_at DESC, id DESC`; each item contains server-computed `allowed_actions` and an `action_targets` map containing only those allowed actions. Each target is a screened public domain reference `{kind:"task"|"approval"|"output"|"run"|"plan", id, revision, run_revision?, next_states?, file_id?}` sufficient for the existing authorized mutation endpoint; `next_states` exists only for `update_work` and contains the exact legal durable task transitions, while `file_id` exists only for `verify_output` and binds that output to its exact opaque `FileItem`. The client never infers a hidden linked record, reconstructs transitions from the collapsed Work bucket, or trusts a URL-supplied target. A COMPLETE conversation assignment projects as one task-backed Work item; its linked run status, handoffs, approvals, and outputs enrich that item's detail/history and never create a second top-level assignment row. Standalone legacy/manual runs without an assignment task remain independent Work items. |
| `GET /projects/{project_id}/conversation?before=&limit=` | none | `{items: ConversationMessage[], next_before: string\|null}` containing root messages only, ordered oldest-to-newest within the returned page; `before` is `(created_at,id)` exclusive. Each root projects the authoritative reply count and at most the three latest authorized replies. |
| `POST /projects/{project_id}/conversation/messages` | `{client_request_id, body, mode:"message"\|"assignment", assignee_agent_ids, reviewer_human_ids, source_message_id:null\|string}` | `{message, work_item:null\|WorkItem}`; the same `(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)` and body returns the original result; a body mismatch under that full identity is `409 idempotency_mismatch`. |
| `POST /projects/{project_id}/conversation/messages/{message_id}/replies` | `{client_request_id, body}` | `{message}`; parent must be a root or direct reply; replies always retain root depth one. |
| `GET /projects/{project_id}/conversation/messages/{message_id}/replies?before=&limit=` | none | `{items: ConversationMessage[], next_before: string\|null}` for the canonical root. A root or its direct reply may be supplied as `message_id`; the server resolves it to that root. Replies are ordered oldest-to-newest within the returned page; `before` is `(created_at,id)` exclusive. This endpoint is the authoritative paginated thread read; replies are not duplicated as top-level conversation rows. |
| `PATCH /projects/{project_id}/conversation/messages/{message_id}` | `{client_request_id, expected_revision, body}` | `{message}`; stale `expected_revision` is `409 revision_conflict`; append `ConversationMessageAudit` in the same state replacement as the message update; the same full idempotency identity replays the prior response. |
| `DELETE /projects/{project_id}/conversation/messages/{message_id}` | `{client_request_id, expected_revision}` | `{message}` with durable tombstone; stale revision conflicts, duplicate delete under the same full idempotency identity replays, and linked work/output/evidence links remain unchanged. |
| `PUT /projects/{project_id}/conversation/messages/{message_id}/reactions/{reaction}` | `{client_request_id}`; `message_id` and fixed-enum `reaction` are route targets | `{message}`; operation is `conversation_reaction_put`. The canonical request hash includes method, route targets, and body; the same full identity replays, while a hash mismatch is `409 idempotency_mismatch`. |
| `DELETE /projects/{project_id}/conversation/messages/{message_id}/reactions/{reaction}` | `{client_request_id}`; `message_id` and fixed-enum `reaction` are route targets | `{message}`; operation is `conversation_reaction_delete`. The canonical request hash includes method, route targets, and body; the same full identity replays, while a hash mismatch is `409 idempotency_mismatch`. |
| `PUT /projects/{project_id}/conversation/messages/{message_id}/saved` | `{client_request_id}`; `message_id` is the route target | `{saved: true}` for the current human only; operation is `conversation_saved_put`. The canonical request hash includes method, route target, and body; the same full identity replays, while a hash mismatch is `409 idempotency_mismatch`. |
| `DELETE /projects/{project_id}/conversation/messages/{message_id}/saved` | `{client_request_id}`; `message_id` is the route target | `{saved: false}` for the current human only; operation is `conversation_saved_delete`. The canonical request hash includes method, route target, and body; the same full identity replays, while a hash mismatch is `409 idempotency_mismatch`. |
| `POST /projects/{project_id}/upload` | `multipart/form-data`: `files[]`, `client_request_id`, optional `reingest`; project, tenant, and human come from the route/current auth | `{uploaded, files, errors, project_id}`. Operation is `project_source_upload`; same full identity/content replays exact metadata, changed canonical body/hash is `409 idempotency_mismatch`, and an existing target is never overwritten. |
| `GET /projects/{project_id}/files?kind={source\|output\|evidence\|all}` | none | `{items: FileItem[], files: LegacyDataRoomFile[]}` ordered `kind ASC, updated_at DESC, id DESC`. `items` is the W4 authorized Sources/Outputs/Evidence inventory and contains only opaque IDs plus public metadata/provenance; each item also contains server-computed `allowed_actions` and `action_targets`. Only an awaiting-verification output that the current human may verify receives `verify_output`, bound to the exact output mutation ID and version. Sources, evidence, producers, viewers, and non-awaiting outputs receive no verification action. The compatibility-only `files` field retains the legacy source-inventory shape until its existing console callers migrate. The W4 workplace UI consumes only `items`. |
| `GET /projects/{project_id}/files/{file_id}` | none | `{file: FileItem}` for the exact opaque ID, never a filesystem ref. `FileItem` contains `id`, `mission_id`, `kind`, `name`, `media_type`, `size`, `sha256`, `state`, `version`, public producer/verifier attribution when available, `source_ids`, `introduced_by_message_id`, timestamps, `previewable`, `downloadable`, `allowed_actions`, and screened `action_targets`; it contains no artifact/path/host/runtime field. The Files review surface renders the exact candidate metadata, sources, and evidence before decision controls, and mutation authority comes only from this returned FileItem—not the URL or Work title. |
| `GET /projects/{project_id}/files/{file_id}/content?disposition={inline\|attachment}` | none | Exact immutable bytes with authorization, hash recheck, private caching, `nosniff`, and safe disposition. |

W4 review-action correction is RED-first with `test_work_action_targets_publish_only_exact_legal_next_states`, `test_work_verify_target_binds_exact_opaque_file_item`, `test_file_item_verify_action_is_current_human_and_state_scoped`, `test_task_owner_cannot_review_own_work_but_distinct_reviewer_can`, `work_review_submits_exact_public_decisions`, `work_update_uses_only_server_next_states`, `file_verification_requires_exact_returned_candidate_and_evidence`, and the parameterized exact output-state label matrix. These tests close the gap between a visible affordance and an executable authorized action: the Work surface may route a human into review, but only a distinct authorized reviewer receives `review_work`, only the returned FileItem may authorize final verification, and every output lifecycle state is named honestly.

Final W4 review-boundary clarification: `in_review -> done` is not a generic task transition. Only `review_task(..., decision="approve")` by a distinct authorized human reviewer may complete reviewed work. The service exposes no self-review override and rejects non-human review actors regardless of caller. Review completion is one recoverable repository transaction: while holding the project write lock it rechecks current membership/role, exact task identity/revision/state, then durably commits the review record before publishing the resulting task state. A crash before the review record leaves the task `in_review`; a crash after the review record is durable is recovered idempotently before task/review reads or later task writes. Recovery reconstructs the sole legal target from the persisted current Task plus exact Review decision; it preserves every non-review Task field, appends exactly one deterministic review activity, and rejects a journal whose state, ownership, title, objective, result, activity, or revision differs. Before W6 begins, the completed W5 lane temporarily transfers narrow edits in `simulacra/collaboration/repository.py`, `simulacra/collaboration/service.py`, and `tests/test_collaboration_domain.py`, plus the reserved W6 route-test path `tests/test_cmul8_api_routes.py`, to W4 solely for this enforcement and its service/API crash/race/corrupt-journal regressions; W4 returns all four paths to W6 immediately after its gate. No room, event, or other task-lifecycle behavior changes in this correction.
| `POST /projects/{project_id}/preview/exchanges` | empty JSON body; requires current authenticated project membership and an exact promoted/approved app revision | Control-plane response `{exchange_id, exchange_proof, preview_origin}`. `exchange_id`/proof are short-lived, one-time, HMAC-bound to tenant, project, current actor, promoted revision, and configured `PREVIEW_ORIGIN`; they are returned only to authenticated JavaScript and never put in a route, query, iframe `src`, clipboard, or logs. Both control-plane session cookies and preview capability cookies are host-only. The endpoint fails closed while `PREVIEW_ORIGIN` is absent, has the same hostname as control, fails the same-site distinct-subdomain environment contract, or `workplace_preview_origin_v1` is off. |
| `OPTIONS {PREVIEW_ORIGIN}/preview/exchange` | browser preflight from the exact configured control origin | `204` only with `Access-Control-Allow-Origin: {CONTROL_ORIGIN}`, `Access-Control-Allow-Credentials: true`, `Access-Control-Allow-Methods: POST, OPTIONS`, `Access-Control-Allow-Headers: content-type`, `Vary: Origin`, and a bounded `Access-Control-Max-Age` of 300 seconds. No wildcard, reflected arbitrary origin, `null`, unlisted header, or unlisted method is accepted. |
| `POST {PREVIEW_ORIGIN}/preview/exchange` | JSON `{exchange_id, exchange_proof}` sent by PreviewDrawer/RightPanel/FilePreview with `credentials:"include"` from the exact configured control origin | Preview origin atomically consumes the one-time exchange after internal signed membership/revision validation and returns `204` plus a host-only, HttpOnly, Secure, `SameSite=None` capability cookie scoped to `/projects/{project_id}/preview`. Its actual response repeats exact `Access-Control-Allow-Origin: {CONTROL_ORIGIN}`, `Access-Control-Allow-Credentials: true`, and `Vary: Origin`; it never uses `*`, reflects arbitrary origins, or permits `null`. The preview origin contains only promoted app bytes and preview capability state—never control-plane cookies, APIs, session data, or tenant administration. |
| `GET {PREVIEW_ORIGIN}/projects/{project_id}/preview`, `/preview/`, `/preview/{full_path:path}` | host-only preview-origin capability cookie; every request rechecks capability, internal signed current-membership/revocation state, tenant/project/revision/path scope, and fixed promoted-app root | Serves only exact claim-bound promoted app bytes, never staging/unverified output. W4’s preview Vite build uses `base: "./"`, so nested module JS/CSS/image/data paths stay beneath the cookie-scoped preview route. Responses set `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, and CSP `default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors {CONTROL_ORIGIN}`. Add `img-src 'self' data:` and/or `font-src 'self' data:` only when the deterministic fixture proves either required; do not add `unsafe-inline` unless the generated build proves a need and a per-build hash strategy is specified. Iframes retain `sandbox="allow-scripts allow-forms allow-same-origin"` because promoted apps may submit same-preview-origin forms; an external form action is denied by CSP and must have exact W4 security/browser proof. |
| `GET /workspace/events` | `Last-Event-ID` header | `text/event-stream`; events are wake-up envelopes `{id,type,mission_id,occurred_at}` only. Heartbeat every 20 seconds. |
| `POST /projects/{project_id}/cmul8/room/presence` | none | `{presence: {actor_id, status:"online"\|"away"\|"offline", last_seen_at}}`; current authenticated room member only; the server writes `last_seen_at` and derives status, never trusting a client actor or status. |

`PREVIEW_ORIGIN` must use a different hostname from the control origin but be a same-site sibling subdomain under the configured registrable domain (for example, `app.example.test` and `preview.example.test`). This gives the embedded preview a browser-valid host-only `SameSite=None` capability cookie without sending it to the control host; the control host’s session cookies are likewise host-only. The compatibility strategy is deliberately same-site: Storage Access API is not required or requested. Cross-site preview deployments are unsupported, and validation keeps `workplace_preview_origin_v1` off. The W4 real-browser fixture must prove the exchange and nested assets work with the cookie in the supported same-site layout and that the control host never receives the preview cookie.

`POST /projects/{project_id}/conversation/messages` is the only public path that may create an agent run from a conversation action. For assignment mode it delegates to the recoverable coordinator in the preceding section; it does not pretend the two repositories share one atomic filesystem transaction. A message-mode post creates no run, regardless of text. `PATCH` and `DELETE` use the same canonical `client_request_id` idempotency rule. Existing `POST /projects/{project_id}/mission/runs` remains for established direct-run behavior during the transition and is hidden behind `workplace_conversation_v1`; it is not duplicated in the new console composer.

#### Feature flags and deletion decisions

NEW `simulacra/workplace/config.py` owns server-only flag resolution and NEW `tests/test_workplace_config.py` proves defaults and isolation. The server exposes only booleans in bootstrap/config: `workplace_shell_v1`, `workplace_attention_v1`, `workplace_conversation_v1`, `workplace_files_v1`, `workplace_preview_origin_v1`, `workplace_sse_v1`, and `workplace_bootstrap_v1`. EDIT `apps/api/main.py` injects flags only after authenticated tenant resolution. They default off in production; W1 defines resolution only, while the W7 release lane alone activates an internal tenant allowlist through its environment contract. They are removal candidates only after two stable releases and full migration evidence, never public controls or client-writable settings.

W1D creates NEW `apps/api/workplace_routes.py` as the stable aggregator with an immutable registration list of summary, conversation, work, file, and event subrouter module paths. Its `register_if_present` loader skips only a `ModuleNotFoundError` whose missing-module name is exactly the listed optional subrouter module; it re-raises a nested dependency `ModuleNotFoundError` and every other import-time error. It otherwise mounts the listed router, keeping the W1D merge bootable; later lanes add only their own subrouter module and never edit the aggregator. Feature flags remain default-off throughout this registration behavior. After W1D has merged its flag mount, ownership of EDIT `apps/api/main.py` transfers to W4 for the preference-router mount **and** preview-session/asset security routes; EDIT `apps/api/cmul8_routes.py` and EDIT `tests/test_cmul8_api_routes.py` transfer to W6 at that same W1D merge. NEW `apps/api/preference_routes.py` exposes the three preference endpoints above and derives both tenant and human solely from current authentication. NEW `simulacra/workplace/preferences.py` owns `JsonWorkplacePreferenceRepository`; its `preferences_state.json` is per tenant and human at `RUNS_DIR/.workplace-control/preferences/{tenant_id}/{human_id}/state.json`, never a shared client-addressable file. NEW `tests/test_workplace_preferences.py` writes first: `test_work_view_preference_cas_persists_across_service_restart`, `test_notification_preference_cas_and_mute_suppresses_delivery_not_attention`, `test_preference_routes_use_current_human_and_reject_nonmember_mute`, and `test_preference_state_replace_is_crash_atomic`. W6 consumes the stable repository/API read contract for `NotificationPreferences.tsx`; it neither edits the repository, router, mount, nor W4 preference tests.

- Delete/deprecate the legacy mixed comment/event timeline only after `workplace_conversation_v1` is globally on, `GET /conversation` has parity evidence, and no console import uses it. Preserve its underlying event records.
- Delete/deprecate `ActivityInbox.tsx` only after all call sites use `AttentionInbox.tsx`; do not ship both as active global inboxes.
- Do not create a second generic task table, a second artifact store, public runtime settings, public Graph/Observability navigation, or a client-side authorization engine.

#### Invitations, notification outbox, and preference contracts

EDIT `simulacra/collaboration/models.py`, repository, and service add an `Invitation` record with `id`, `tenant_id`, `project_id`, `invited_by`, normalized `invitee_email`, `requested_role`, a 256-bit CSPRNG single-use `accept_token_digest` (SHA-256), `status` (`pending`, `accepted`, `revoked`, `expired`), `expires_at`, `accepted_actor_id`, `revision`, and timestamps. The raw token exists only in the invitation URL/body and is never persisted or logged; only a constant-time digest comparison is retained. EDIT `apps/api/cmul8_routes.py` exposes `POST /projects/{project_id}/cmul8/room/invitations {client_request_id, email, role}`, `POST /projects/{project_id}/cmul8/room/invitations/{invitation_id}/accept {client_request_id, token}`, `POST /projects/{project_id}/cmul8/room/invitations/{invitation_id}/revoke {client_request_id, expected_revision}`, and `POST /projects/{project_id}/cmul8/room/members/{actor_id}/remove {client_request_id, expected_room_revision}`.

The accepted membership gate is concrete and expand-only. EDIT `simulacra/demo/identity.py` adds `Membership.transaction_id: string\|null = null` and `Membership.visibility_state: "pending_commit"\|"committed" = "committed"`; EDIT `simulacra/demo/db.py` JSON membership serialization stores those fields; EDIT `simulacra/demo/pg_store.py` adds nullable `transaction_id` and non-null `visibility_state TEXT DEFAULT 'committed'` to the existing membership row/schema; and EDIT `simulacra/collaboration/models.py` adds the same two fields/defaults to `Member`, persisted by EDIT `simulacra/collaboration/repository.py` in `room.json`. Old JSON/PG rows missing either field deserialize as `transaction_id=null, visibility_state="committed"` and remain visible. The sole journal lookup helper is NEW `invitation_acceptance.py:is_acceptance_complete(tenant_id, project_id, transaction_id)`, which reads the atomically replaced acceptance journal under the tenant acceptance coordinator lock. Every membership/room authorization reader in identity, `apps/api/security.py`, and collaboration repository applies exactly this rule: a record with no `transaction_id` is visible; a tagged record is visible only when `visibility_state="committed"` and its matching tenant/project journal is exactly `COMPLETE`; all other tagged records are absent from lists and authorization decisions. No public reader may infer a pending row exists.

EDIT `simulacra/demo/identity.py`, `simulacra/demo/clerk_auth.py`, `simulacra/demo/db.py`, `simulacra/demo/pg_store.py`, and `apps/api/security.py` add a narrow pre-membership `InvitationAcceptPrincipal`, distinct from `AuthContext`. `require_invitation_accept_authenticated_email` verifies the bearer credential but never calls the normal tenant-membership resolver or accepts email/actor/tenant from the body. Clerk proof is fail-closed: `verify_clerk_jwt` must validate RS256 signature against configured JWKS, `exp`, exact configured issuer, and exact configured audience; it then requires a nonempty exact `sub` and a verified primary email from either a signed, configured Clerk JWT-template claim or a server-to-server Clerk lookup bound to that same verified subject. It must reject arbitrary `email`/`primary_email_address` claims, any synthetic `sub@users...` fallback, unverified email, and subject/email mismatch. `identity.py` persists normalized verified provider email, verification timestamp, and provider subject in the existing JSON user store; `db.py`/`pg_store.py` add expand-only users columns and backfill no historical user as verified. `clerk_auth.py` records those fields without its current automatic membership upsert on this pre-membership path. Local development/test acceptance is permitted only for a server-side fixture/admin enrollment flag plus an explicit non-production allow flag; it is default-disabled, production-denied, and never user self-asserted. This principal can call only acceptance; every ordinary room/Mission route still requires the standard membership-bearing `AuthContext`. Tenant/project/token/email mismatch returns the identical public `404 invitation_unavailable`.

Missing issuer, audience, trusted-email-template configuration, or provider lookup credentials fails closed for invitation acceptance; the ordinary authenticated product session is not upgraded to an invitation principal by a permissive fallback. The RED proof in EDIT `tests/test_identity.py` includes `test_clerk_invitation_principal_rejects_invalid_signature`, `test_clerk_invitation_principal_rejects_expired_token`, `test_clerk_invitation_principal_rejects_missing_subject`, `test_clerk_invitation_principal_rejects_missing_trusted_email_proof_config`, and `test_clerk_provider_lookup_is_bound_to_verified_subject` in addition to the existing issuer/audience, unverified/fallback-email, subject-mismatch, and production-local-path denial tests.

NEW `simulacra/collaboration/invitation_acceptance.py` owns the recoverable acceptance coordinator. Its exact lock order is **tenant acceptance coordinator flock → identity user-store/PG membership lock → collaboration room lock**. It journals `(tenant_id, project_id, authenticated_human_actor_id, operation="invitation_accept", client_request_id)`, the invitation digest/expiry/revision, and intended enrollment/member records. States are `PREPARED → COMMIT_DECIDED → STORES_DURABLE → COMPLETE` or `ABORTED`. Room membership remains in the existing `JsonCollaborationRepository` `room.json` even when identity/memberships use PostgreSQL; no PG room table is introduced. Therefore neither JSON mode nor PG-identity mode claims a single database transaction across enrollment and room state: the exact same transaction tag and atomically read `COMPLETE` journal gate hide both from normal tenant/room authorization until durable. Thus no reader observes tenant enrollment without room membership, including crashes after either identity-store/PG commit or filesystem room replacement. In PG-identity mode, recovery treats the committed membership row plus room.json as cross-store inputs and completes the journal/projection rather than rolling either back. Recovery before `COMMIT_DECIDED` revalidates the verified principal, token digest, tenant/project, expiry/revocation, and invitation revision, then resumes or writes `ABORTED`; recovery after it completes without rollback. A valid first accept consumes the token once and records `accepted_actor_id`; a same full idempotency identity replay returns accepted membership, while a different actor or any reuse after acceptance, expiry, or revocation returns `404 invitation_unavailable`. Revoke applies only to pending invitations; an accepted invitation is never retroactively revoked. Member removal is a separate owner/admin-only action, cannot remove the last owner, is idempotent on replay, and immediately denies Mission/work/attention/conversation/file aggregate reads through existing room-membership authorization. Search revocation remains WP-13 work outside this 10-day scope.

Ordinary room mutations must preserve, not discard, raw pending rows. Under the collaboration room lock, repository read-modify-write operations retain every serialized `Member` record including transaction-tagged `pending_commit` members, even though authorization/list projections filter those rows until the coordinator is `COMPLETE`. NEW `tests/test_invitation_acceptance.py:test_concurrent_room_mutation_preserves_hidden_pending_member_rows_while_authorized_read_filters_them` uses a real `tmp_path` filesystem and two processes: one holds an incomplete acceptance row while the other performs an ordinary room-role/member mutation; raw `room.json` retains the pending member unchanged, normal authorized reads omit it, and replay to `COMPLETE` exposes it exactly once.

NEW `tests/test_invitation_acceptance.py` writes first: `test_stolen_token_wrong_verified_email_is_unavailable`, `test_cross_tenant_token_use_is_unavailable`, `test_revoked_or_expired_invitation_is_unavailable`, `test_concurrent_accept_is_single_use_and_enrolls_once`, `test_accept_crash_before_and_after_each_identity_room_journal_boundary`, `test_concurrent_readers_never_observe_tenant_membership_without_room_membership`, `test_pending_transaction_rows_are_hidden_from_identity_and_room_readers`, `test_complete_transaction_rows_become_visible_after_restart`, `test_concurrent_room_mutation_preserves_hidden_pending_member_rows_while_authorized_read_filters_them`, and `test_pg_identity_with_filesystem_room_crash_recovery_is_complete_gated`. EDIT `tests/test_identity.py` adds `test_clerk_invitation_principal_rejects_invalid_signature`, `test_clerk_invitation_principal_rejects_expired_token`, `test_clerk_invitation_principal_rejects_missing_subject`, `test_clerk_invitation_principal_rejects_missing_trusted_email_proof_config`, `test_clerk_provider_lookup_is_bound_to_verified_subject`, `test_clerk_invitation_principal_rejects_invalid_issuer_or_audience`, `test_clerk_invitation_principal_rejects_unverified_or_synthetic_fallback_email`, `test_clerk_invitation_principal_rejects_subject_mismatch`, and `test_local_invitation_email_verification_is_denied_in_production`; EDIT `tests/test_db.py` adds `test_user_verified_email_columns_are_expand_only` and `test_legacy_membership_defaults_to_committed_visibility`; EDIT `tests/test_cmul8_api_routes.py` adds `test_invitation_accept_dependency_allows_pre_membership_but_returns_anti_enumeration_errors`. EDIT `tests/test_collaboration_domain.py` adds `test_legacy_room_member_without_transaction_remains_visible` and retains invite authority/staleness/replay plus member-removal/last-owner/access-loss tests. All are in the W6 command and use real temporary files for journal/restart boundaries.

NEW `simulacra/collaboration/notifications.py` owns a durable notification projector cursor and external `NotificationOutbox` records: `id` (the durable external `delivery_id`), `event_id`, `recipient_id`, `channel`, `dedupe_key`, `payload`, `status` (`pending`, `leased`, `delivered`, `dead_letter`), `attempt_count`, `lease_expires_at`, provider receipt metadata, and timestamps. In-app notification is not an outbox delivery: it is the authoritative attention row returned by the existing `GET /workspace/attention` and read by `POST /workspace/attention/read`, deduplicated by `(source_event_id, current_human_id)`. The source event is authoritative; the projector derives missing external outbox rows idempotently with `dedupe_key = event_id:recipient_id:channel`. A crash after event/before outbox or after outbox/before cursor is repaired by replay. The projector applies this exact preference matrix only to external channels: `all_actionable` includes every actionable event; `mentions_and_decisions` includes only direct mentions and decision-required events; `off` creates no external delivery row; and a Mission ID in `muted_mission_ids` suppresses every external delivery for that Mission regardless of selection. In every case the authoritative attention row remains actionable; Mission mute is therefore orthogonal to event selection.

Projector and external delivery use separate per-project flock-protected cursor/outbox locks; delivery lease acquisition is atomic and idempotent and never creates source actions. The external adapter receives `delivery_id` as its durable provider idempotency key. A provider that supports that key must deduplicate retries. Where a provider does not support deduplication, persisted receipt/inflight/lease state prevents concurrent sends but a crash after provider handoff can cause a redelivery; email is consequently not claimed exactly-once. EDIT `simulacra/deploy_process.py` adds both bounded projector and external-delivery ticks beside its Mission cron scheduler; neither runs in an API request. Tests use real temporary filesystems, two processes, and a deterministic adapter that records sends without SMTP/push/network, injecting crashes at each event/outbox/cursor/lease replacement boundary. W4's `JsonWorkplacePreferenceRepository` is the sole source for private `event_selection`/channel/digest/Mission-mute preferences; notification projection reads its stable contract.

EDIT `simulacra/collaboration/presence.py` and EDIT `apps/api/cmul8_routes.py` define server-derived room presence. `POST /projects/{project_id}/cmul8/room/presence` accepts no actor or status field: current authenticated room membership records the server-clock `last_seen_at`. A presence reader reports `online` when age is `<=45` seconds, `away` when age is `>45` and `<=180` seconds, and `offline` when age is `>180` seconds, when no record exists, or after process restart before a new heartbeat. It is display-only and never authorizes an action. EDIT `apps/console/src/api.ts` and EDIT `apps/console/src/features/project-room/ProjectRoomContainer.tsx` send a heartbeat every 30 seconds only while the document is visible; `visibilitychange` to hidden cancels the timer, and disconnect/page close relies on expiry rather than a client-supplied offline claim. EDIT `apps/console/src/features/team/TeamRoster.tsx` and NEW `apps/console/src/features/workplace/crew/CrewRail.tsx` render only the server status. RED tests are `test_presence_threshold_boundaries_are_server_derived` and `test_presence_restart_returns_offline_without_authorization_effect` in EDIT `tests/test_collaboration_domain.py`, `test_presence_two_humans_are_scoped_and_membership_filtered` in EDIT `tests/test_cmul8_api_routes.py`, and `presence_heartbeat_stops_when_hidden_and_roster_renders_server_status` in NEW `apps/console/src/features/workplace/crew/CrewRail.test.tsx`.

EDIT `apps/console/src/components/GuestAuthGate.tsx`, EDIT `apps/console/src/components/LoginPage.tsx`, EDIT `apps/console/src/components/ProfileManageModal.tsx`, and EDIT `apps/console/src/features/team/TeamRoster.tsx` to render invitation recovery, accepted/revoked membership, first-workspace checklist, profile state, and preferences. NEW `apps/console/src/features/workplace/onboarding/OnboardingChecklist.tsx` owns the three readiness steps; NEW `apps/console/src/features/workplace/onboarding/NotificationPreferences.tsx` owns event selection, channels/digest, and per-Mission mute controls. These components consume server-provided allowed actions and flags only; they contain no authorization or runtime configuration logic.

Only W7 starts NEW `tests/support/workplace_fixture_runtime.py` from the W4-transferred NEW `apps/console/e2e/global-setup.ts`. It serves fixed fixture data and a fake Codex harness and rejects provider hostnames, external HTTP, and a non-fixture runtime root. In W7, the transferred Playwright configuration may start only that fixture runtime and Vite; any observed real provider/network request fails the suite. W0 and W4 use only their local two-origin frontend/Playwright harness and make no Python fixture-runtime or provider/network-denial claim. NEW `docs/MISSIONS_WORKPLACE_ROLLOUT.md` is the required future rollout/rollback runbook path; this planning task does not create it.

### 13.4 Deterministic test assets and commands

Create these fixtures before implementation work begins:

| Fixture | Location | Contents |
| --- | --- | --- |
| `mission_workplace.json` | NEW `tests/fixtures/mission_workplace.json` | Fixed tenant `tenant_demo`, three Missions, two humans, two agents, one approval, one staged and one verified output, timestamps from `2026-01-02T09:00:00Z` through `09:07:00Z`. |
| `conversation_cases.json` | NEW `tests/fixtures/conversation_cases.json` | Root/reply, duplicate request, ordered two-agent assignment, edit/delete audit evidence, tombstone/link preservation, reactions, and private save states. |
| `assignment_transaction.json` | NEW `tests/fixtures/assignment_transaction.json` | Fixed journal states PREPARED, COMMIT_DECIDED, STORES_DURABLE, COMPLETE, and ABORTED with pinned graph hash, reserved IDs, and injected crash expectations. The `queued_before_complete` case means `journal.state=STORES_DURABLE` and `run.status=queued`. |
| `workplace.ts` | NEW `apps/console/src/test/fixtures/workplace.ts` | TypeScript mirror of public API responses; no internal IDs/paths/runtime metadata. |
| fake clock and event stream | NEW `apps/console/src/test/fakeClock.ts`; NEW `apps/console/src/test/fakeEventSource.ts` | Manually advanced UTC clock, emitted wake-up events, and reconnect failure counter; no clock/network dependency. |

Use the following commands exactly. CI runs the aggregate commands in the wave gate; an owner runs the targeted command during RED/GREEN work.

```sh
uv run pytest tests/test_mission_projections.py -q
uv run pytest tests/test_conversation_service.py -q
uv run pytest tests/test_assignment_coordinator.py -q
uv run pytest tests/test_workspace_bootstrap_coordinator.py -q
uv run pytest tests/test_source_staging.py -q
uv run pytest tests/test_operation_graph_contract.py -q
uv run pytest tests/test_operation_graph_store.py -q
uv run pytest tests/test_preview_origin_routes.py -q
uv run pytest tests/test_workplace_summary_routes.py -q
uv run pytest tests/test_conversation_api_routes.py -q
uv run pytest tests/test_work_file_routes.py -q
uv run pytest tests/test_workplace_event_routes.py -q
uv run pytest tests/test_workplace_preferences.py -q
uv run pytest tests/test_invitation_acceptance.py -q
uv run pytest tests/test_notification_outbox.py tests/test_workplace_config.py -q
uv run pytest tests/test_workplace_fixture_runtime.py -q
uv run pytest tests/test_workplace_rollout.py -q
uv run pytest tests/test_workplace_real_worker_integration.py -q
uv run pytest tests/test_workplace_api_routes.py tests/test_mission_api_routes.py -q
uv run pytest tests/test_missions_v0.py tests/test_collaboration_domain.py tests/test_cmul8_api_routes.py tests/test_mission_execution.py -q
uv run pytest -q
npm --prefix apps/console run test:unit
npm --prefix apps/console run typecheck
npm --prefix apps/console run build
npm --prefix apps/console run test:e2e -- workplace-loop.spec.ts
```

Repository inspection records that the current `apps/console/package.json` has no test scripts or test dependencies. W0's Browser-test/tooling owner EDITs `apps/console/package.json` and EDITs `apps/console/package-lock.json` by adding exact dev dependencies `vitest@3.0.7`, `@testing-library/react@16.2.0`, `@testing-library/jest-dom@6.6.3`, `jsdom@26.0.0`, `@playwright/test@1.50.1`, and `@axe-core/playwright@4.10.1`, plus scripts `test:unit: vitest run`, `typecheck: tsc --noEmit`, and `test:e2e: playwright test`. Its smoke sequence is `npm --prefix apps/console install --save-dev --save-exact vitest@3.0.7 @testing-library/react@16.2.0 @testing-library/jest-dom@6.6.3 jsdom@26.0.0 @playwright/test@1.50.1 @axe-core/playwright@4.10.1`, `npm --prefix apps/console exec playwright install chromium`, `npm --prefix apps/console run test:unit`, `npm --prefix apps/console run typecheck`, `npm --prefix apps/console exec playwright -- --version`, `npm --prefix apps/console run test:e2e -- harness-smoke.spec.ts`, and `npm --prefix apps/console run build`; the lockfile is generated only by that exact install, never hand-edited.

W0 has exactly five RED/GREEN product tests: `fake_clock_and_event_source_are_deterministic` in NEW `apps/console/src/test/harness.test.tsx`; `tokens_imports_and_meets_contrast_contract` in NEW `apps/console/src/design/tokens.test.ts`; `typography_imports_and_exposes_role_classes` in NEW `apps/console/src/design/typography.test.ts`; `motion_respects_reduced_motion` in NEW `apps/console/src/design/motion.test.ts`; and `primitives_render_focus_and_state_contracts` in NEW `apps/console/src/design/primitives.test.ts`. Its GREEN/config commit also creates the minimal two-origin **frontend** harness, global setup, Playwright config, Vite mode seam, and NEW `apps/console/e2e/harness-smoke.spec.ts` with `two_origin_frontend_harness_starts`; that smoke only proves both local frontend origins start and exchange a test page, and is not a sixth product RED/GREEN test. W0 creates no Python fixture runtime and asserts no provider/network denial. It transfers the browser paths to W4 before W4 writes preview-origin RED tests. `WorkplaceShell.test.tsx` begins in W2 after that component exists. W7 alone uses its fixture runtime/fake Codex harness to prove UI/multiplayer/retry/accessibility without a provider or network. The eight Section 6.15 composite boards run at 1440×1000, 1180×820, 834×1112, and 390×844, producing exactly 32 visual-diff baseline screenshots; journey screenshots are separate evidence artifacts. The separate real-worker command is `uv run pytest tests/test_workplace_real_worker_integration.py -q`.

Because this plan document is currently untracked, whitespace verification has two distinct commands. `git diff --check -- docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md` is the tracked-change check and may have no input until the file is staged. `git diff --no-index --check /dev/null docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md` is the untracked-aware check; its exit status **1 is expected** because `/dev/null` differs from an added file, and any whitespace diagnostic is a failure.

### 13.5 Dependency DAG and wave merge order

```text
W0 baseline + shared contracts
 ├─ W1A Mission models/repository/service/projections GREEN ── Mission paths transfer ── W1C assignment coordinator
 │                                                        └─ projections/tests transfer ── W2 summary routes ── transfer ── W4 work/file projections
 ├─ W1B collaboration records (after shared contracts; independent of W1A) ────────────────┐
 └─ W1 flags ───────────────────────────────────────────────────────────────────────────────┤
W1B + W1C + flags ── W1D static aggregator/security envelope ──┬─ W2 summary routes + global shell ── transfer AgentShell ── W3 conversation routes + conversation UI
                                                                  ├─ W4 work/file/preference routes + workspace UI (after W2 projections/tests transfer)
                                                                  └─ W5 waits for W2 `useWorkplaceQuery` + W3 conversation-route transfers
W3 ── transfer conversation router/tests ── W5 threads/saved/SSE ── transfer collaboration paths ──┐
W4 + W5 ──────────────────────────────────────────────────────────── W6 invitations/presence/notifications/onboarding full gate
W6 verified-principal foundation + W4 main/test transfer ── W6B workspace bootstrap coordinator + bounded recovery tick/source staging
W2 ── transfer App.tsx ──┐
W6B full gate ───────────┴─ earliest Day 11 / next execution window ── W7R runtime integration + W7 browser/accessibility/rollout
```

Merge order is strict within each wave; independent listed lanes may merge in either order after their common gate.

| Wave | Merge order | Safe parallel lanes | Required merge gate |
| --- | --- | --- | --- |
| W0 | one PR: `harness test` → `fixture/fake utilities` → `test script/config plus minimal two-origin frontend/global-setup/Playwright/Vite seam and browser smoke` → GREEN evidence → transfer browser paths to W4 | No parallel W0 merge; authors may pair on one owned PR | NEW `harness.test.tsx` is GREEN; unit, `tsc --noEmit`, local two-origin browser smoke, and console build pass. W0 makes no Python fixture-runtime or provider/network-denial claim. |
| W1 | `shared contracts` → `W1A Mission models/repository/service/projections GREEN` → transfer Mission models/repository/service to W1C and projections/tests to W2; W1B conversation records may run after shared contracts independently → `W1C assignment coordinator` → `HTTP serializers` → `TS contracts` | W1B and server-flag work may run after common contracts; W1C cannot start or merge Mission-path edits before the W1A transfer; W2 cannot edit projections/tests before its recorded transfer; invitation work is reserved for W6 | Both W1A transfers are recorded, W1B/W1C RED tests turned GREEN, fault-injection/two-process coordinator tests pass, and public-field denylist plus old Mission API tests pass. |
| W2 | transferred `projections.py`/`test_mission_projections.py` + `workplace_summary_routes` → `API client` → `WorkplaceShell` → `AttentionInbox` → retire old inbox import → transfer projections/tests to W4 | Shell CSS and attention presentation after `contracts.ts` lands; W4 cannot edit projections/tests before this merge | Pagination/revocation/read-not-resolve/projection tests plus URL/back-forward unit test and build; W2→W4 projection transfer recorded. |
| W3 | after W2 transfers `AgentShell.tsx`: `coordinator-backed assignment service` → `conversation_routes` → `composer/timeline` → legacy-submission removal → mention picker | Backend service and CSS components after the exact request/response and coordinator fixture are frozen | Duplicate-send one-message/one-run proof, PREPARED/STORES_DURABLE/queued invisibility, COMPLETE-only publication/claim, ABORTED replay, two-agent ordering, legacy direct-submit removal, and no-runtime-field proof. |
| W4 | after W1D transfers EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py`, W0 transfers the minimal two-origin browser paths, and W2 transfers `projections.py`/`test_mission_projections.py`: `work/file projections` + `work_routes`/`file_routes` + `preference repository/routes` + dedicated-preview-origin exchange/security → `work list with persisted view/filters` → `files UI` | Work/file lanes after `WorkItem` union is frozen; W4 is the sole later owner of transferred projections/tests, `main.py`, route test, active preview iframes, preview-origin environment contract, Vite preview-build base, and W0 browser harness paths | Projection parity, path/hash/security tests, staged-code non-preview proof, one-time exchange/token-free nested asset preview on a same-site distinct-hostname origin with exact preflight/CORS/CSP/form-action/frame/no-sniff/private-cache proof, real-browser host-only-cookie and external-form-denial behavior, preference CAS/restart/crash matrix, current-human authorization, and mute-does-not-suppress-attention proof. Transfer main/test paths to W6B and environment/Vite/browser paths to W7 only after this gate. |
| W5 | after W3 transfers `conversation_routes.py`/route tests and W2 transfers `useWorkplaceQuery.ts`: `thread/saved/reaction endpoints` → `workplace_event_routes` SSE → `thread drawer/reconnect` | Thread service and SSE route after the recorded transfers; no W2/W3/W5 concurrent edit of either transferred path | Thread depth, private save, reaction uniqueness, route-test regression, and missed-event durable-refresh proof. At merge, collaboration models/repository/service and EDIT `tests/test_collaboration_domain.py` ownership transfer to W6. |
| W6 (depends on W4 and W5) | `verified-principal and acceptance coordinator` → `invitation/member-removal contract/routes` → `notification projector/external delivery` → `presence` → `preferences/profile/onboarding component tests` | Starts only after both W4 and W5 merge; W5 transfers models/repository/service and EDIT `tests/test_collaboration_domain.py`, so no W5/W6 concurrent edits | Verified-email/token safety, acceptance crash/concurrent-reader visibility proof, pending-revoke/member-removal authority, access-loss, server-derived presence threshold/restart/two-human proof, projector replay, provider-key/lease delivery contract, and component/service onboarding proof. |
| W6B (after W6 identity foundation) | `blob-first immutable source stage` → `bootstrap reservation coordinator` → `POST /projects` and status/upload serializers → `project/Mission/room/journal-backed graph-build recovery tick` | W6B starts only after W6 identity foundation and recorded W4/W6 transfers; it is the sole later owner of `apps/api/main.py`, `tests/test_mission_api_routes.py`, `operation_graph_builder.py`, `test_operation_graph_contract.py`, and `deploy_process.py` | Same bootstrap request reserves one project ID; crash/retry/concurrent tests prove one project/Mission/room/graph set, no published record with a missing blob, source immutability/adoption, actor-scoped status, and public `provisioning` until the durable graph revision and COMPLETE. |
| W7 (earliest Day 11 / next execution window, after W6B gate) | `pinned Linux real-worker integration` + NEW fixture runtime plus transferred `global setup/Playwright/Vite seam` → `two-context E2E` → `axe/32-screenshot matrix` → `release-owned allowlist activation` → `rollout runbook` | Runtime-integration and browser/visual work begin only after the complete W6B gate; W7 receives browser paths only after W4’s preview-origin gate, creates its own Python fixture runtime, and the release lane alone changes environment/allowlist | Real-worker deterministic-local-transport gate, fixture runtime network denial, all automated gates, 32 screenshot baselines, manual unfamiliar-user run, reviewed rollout runbook, and flag-off compatibility. |

### 13.6 TDD work packages

Each row is an executable RED → GREEN → REFACTOR sequence. Test names are mandatory; adjacent existing tests remain unchanged unless a contract requires their explicit update.

| ID / depends on | RED tests to write first | Minimum GREEN implementation | REFACTOR / exact command / done evidence |
| --- | --- | --- | --- |
| W0 — deterministic frontend, test-assets, and visual harness | `fake_clock_and_event_source_are_deterministic` in NEW `apps/console/src/test/harness.test.tsx`; `tokens_imports_and_meets_contrast_contract` in NEW `apps/console/src/design/tokens.test.ts`; `typography_imports_and_exposes_role_classes` in NEW `apps/console/src/design/typography.test.ts`; `motion_respects_reduced_motion` in NEW `apps/console/src/design/motion.test.ts`; `primitives_render_focus_and_state_contracts` in NEW `apps/console/src/design/primitives.test.ts` | In one non-RED-only PR, Browser-test/tooling EDITs package manifest/lock with the exact Vitest, Testing Library, jsdom, Playwright, and axe pins plus `typecheck: tsc --noEmit` from 13.4 and adds test config/setup; deterministic-test-assets lane adds every 13.4 fixture/fake; visual lane adds `design/{tokens,typography,motion,primitives}.css` and its four named harness tests. No Python test and no WorkplaceShell behavior test belongs in W0. | Centralize `renderWithFixture`; run `npm --prefix apps/console run test:unit`, `npm --prefix apps/console run typecheck`, and `npm --prefix apps/console run build`. Evidence: test advances fixed time and emits exactly one wake-up; all four named design contracts pass; the exact test-dependency smoke sequence, typecheck, and Vite build succeed. |
| W1A — public contracts | `test_workplace_public_serializers_allow_only_contract_fields`; `test_cursor_rejects_tamper_and_preserves_page_boundary` in NEW `tests/test_mission_projections.py` | NEW `simulacra/missions/projections.py` defines typed summary/work/attention projection DTOs and opaque cursor codec; EDIT Mission models/service/repository for versioned optional records. GREEN/merge Mission paths before their W1C transfer and projections/tests before their exclusive W2 transfer. | Remove route-local dict construction. Run `uv run pytest tests/test_mission_projections.py -q`. Evidence: deterministic two-page IDs are non-overlapping, tampered cursor returns the prescribed error, and W1A→W2 projection ownership is recorded. |
| W1B — conversation persistence | `test_create_message_replays_same_request_id`; `test_stale_edit_conflicts`; `test_edit_audit_contains_actor_request_revision_and_prior_body`; `test_delete_audit_contains_attribution_and_preserves_links`; `test_crash_before_state_replace_leaves_neither_audit_nor_change`; `test_crash_after_state_replace_leaves_both`; `test_idempotent_delete_replays` in NEW `tests/test_conversation_service.py` | NEW `simulacra/collaboration/conversation.py`; EDIT models/repository/service with one crash-atomic `conversation_state.json` holding messages, audits, reactions, saved refs, idempotency, and attention receipts. | Extract canonical hashing and a single state-replacement helper. Run `uv run pytest tests/test_conversation_service.py -q`. Evidence: edit/delete audit and mutation are indivisible, replay is stable, and tombstones retain work/evidence links. |
| W1C — cross-store assignment coordinator (after W1A transfer) | `test_all_precomplete_states_are_hidden_and_nonclaimable`; `test_queued_before_complete_is_not_claimed`; `test_complete_is_the_only_public_and_claimable_state`; `test_invalid_predecision_revalidation_writes_aborted`; `test_replace_and_fsync_fault_injection_recovers_each_boundary`; `test_concurrent_reader_and_worker_claim_converge`; `test_lock_order_is_coordinator_graph_collaboration_mission`; `test_two_humans_same_client_request_id_are_isolated`; `test_cross_operation_client_request_id_reuse_is_isolated`; `test_cross_project_same_client_request_id_is_isolated`; `test_cross_tenant_same_client_request_id_is_isolated` in NEW `tests/test_assignment_coordinator.py`; `test_pending_commit_or_queued_before_complete_is_never_claimed` in EDIT `tests/test_mission_execution.py` | Only after the recorded W1A GREEN/merge transfer, NEW journal enum PREPARED → COMMIT_DECIDED → STORES_DURABLE → COMPLETE or ABORTED; journal identity persists tenant/project/authenticated human actor/operation/request ID and EDIT Mission models/service/repository/worker moves `run.status` from `pending_commit` to `queued` only after STORES_DURABLE. | Run `uv run pytest tests/test_assignment_coordinator.py tests/test_mission_execution.py -q`. Evidence: only an atomically read `COMPLETE` journal enables projection/claim; predecision invalid intent returns stable `transaction_aborted`; the same request ID is isolated across human, operation, project, and tenant. |
| W1D — API and feature-flag contract | `test_workplace_aggregator_mounts_present_subrouters_and_skips_only_missing_target_module`; `test_workspace_routes_enforce_membership_and_bounded_public_fields`; `test_public_error_envelope_excludes_banned_fields` in NEW `tests/test_workplace_api_routes.py`; `test_internal_tenant_allowlist_defaults_off` in NEW `tests/test_workplace_config.py` | NEW `apps/api/workplace_routes.py` is the static aggregator/mount/security envelope only; NEW `simulacra/workplace/config.py`; EDIT `apps/api/main.py` to mount/inject after auth; EDIT mission routes only for shared serializers/error mapping. At W1D merge, transfer EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py` exclusively to W4, and EDIT `apps/api/cmul8_routes.py` plus EDIT `tests/test_cmul8_api_routes.py` exclusively to W6. | Move public allowlists into named functions. Run `uv run pytest tests/test_workplace_api_routes.py tests/test_workplace_config.py tests/test_mission_api_routes.py -q`. Evidence: only the listed missing target module is skipped; nested/import-time failures re-raise; nonmember 403, no banned fields, invalid cursor 400, default-off flags, and recorded W1D transfers. |
| W2 — Missions/Needs you shell | `test_mission_summary_pagination_is_membership_filtered` in transferred NEW `tests/test_mission_projections.py`; `test_attention_read_does_not_resolve_source` in NEW `tests/test_workplace_summary_routes.py`; `opens_needs_you_from_url_and_retains_filter_on_reload` in NEW `apps/console/src/features/workplace/shell/WorkplaceShell.test.tsx` | After W1A transfer, NEW `apps/api/workplace_summary_routes.py` owns `GET /missions` and `GET/POST /workspace/attention` and the transferred projection files; EDIT `apps/console/src/api.ts`; NEW `workplace/shell` and `workplace/attention` modules; EDIT `apps/console/src/App.tsx`, EDIT `apps/console/src/components/Sidebar.tsx`, EDIT `apps/console/src/styles.css`, and retire the EDIT `apps/console/src/features/activity/ActivityInbox.tsx` import only after replacement is live. | Consolidate URL state in `workplace/shell/useWorkplaceQuery.ts`. Run `uv run pytest tests/test_mission_projections.py tests/test_workplace_summary_routes.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck && npm --prefix apps/console run build`. Evidence: exact filtered IDs, independent unread/actionable counts, rerendered route state, and exclusive W2→W4 transfer of `projections.py`/`test_mission_projections.py`. |
| W3 — conversation and recoverable assignment vertical slice | In NEW `tests/test_assignment_coordinator.py`: `test_assignment_is_one_message_one_ordered_run`, `test_lost_response_retry_recovers_same_transaction`, `test_precomplete_assignment_is_not_projected_or_claimable`, `test_queued_before_complete_is_not_claimable`, `test_unapproved_plan_blocks_before_prepared_write`; in NEW `tests/test_conversation_api_routes.py`: `test_message_mode_never_creates_run`, `test_conversation_post_uses_stable_member_ids_not_display_names`, `test_patch_stale_revision_and_delete_replay`; in NEW `apps/console/src/features/workplace/conversation/ConversationComposer.test.tsx`: `composer_shows_assignment_preview_and_uses_agent_ids`, `legacy_agent_shell_submission_is_removed_or_delegates_to_conversation_composer` | Only after W2 transfers EDIT `apps/console/src/components/AgentShell.tsx`, NEW `apps/api/conversation_routes.py` implements `GET/POST/PATCH/DELETE /conversation` and delegates assignment mode to the coordinator; NEW `workplace/conversation` components render durable COMPLETE-only reads; the transferred AgentShell removes its direct legacy run submit and delegates only to the new composer. At W3 merge it transfers the router and route tests exclusively to W5. | Remove direct console calls to legacy run creation for composer submission. Run `uv run pytest tests/test_assignment_coordinator.py tests/test_conversation_api_routes.py tests/test_conversation_service.py tests/test_mission_execution.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck`. Evidence: retry returns same IDs; PREPARED through queued remain hidden; only COMPLETE projects/claims; the legacy direct submission is absent. |
| W4 — Work, Files, preferences, and dedicated-preview-origin security (after W1D `apps/api/main.py`/`tests/test_mission_api_routes.py`, W0 browser-path, and W2 projection-path transfers) | `test_work_projection_has_one_record_per_source_and_server_actions` in transferred NEW `tests/test_mission_projections.py`; `test_file_content_rejects_path_escape_and_hash_change`, `test_staged_code_is_not_previewable`, and `test_work_route_computes_allowed_actions_server_side` in NEW `tests/test_work_file_routes.py`; `test_preview_exchange_requires_current_member_promoted_revision_and_exact_origin`, `test_preview_origin_preflight_allows_only_configured_control_origin`, `test_preview_vite_relative_base_keeps_nested_assets_under_capability_route`, `test_preview_origin_requires_same_site_distinct_hostname_and_cookie_compatibility`, `test_preview_origin_exchange_cors_credentials_and_one_time_consumption`, `test_preview_csp_confines_form_action_to_self`, and `test_preview_origin_denies_expired_revoked_cross_tenant_staging_and_guess_id` in NEW `tests/test_preview_origin_routes.py`; `test_preview_origin_environment_fails_closed_without_distinct_hostname` in transferred EDIT `tests/test_mission_api_routes.py`; `preview_drawer_exchanges_body_only_proof_then_uses_token_free_cross_origin_iframe` in NEW `apps/console/src/components/PreviewDrawer.test.tsx`; `right_panel_exchanges_preview_proof_before_nested_asset_iframe_load` in NEW `apps/console/src/components/RightPanel.test.tsx`; `file_preview_uses_dedicated_origin_sandbox_with_allow_same_origin` in NEW `apps/console/src/features/workplace/files/FilePreview.test.tsx`; `preview_origin_exchange_preflight_cookie_nested_assets_and_external_form_submission_is_denied` in NEW `apps/console/e2e/preview-origin.spec.ts`; `work_list_does_not_offer_disallowed_action` and `work_list_restores_saved_view_and_filters` in NEW `apps/console/src/features/workplace/work/WorkList.test.tsx`; the four named preference tests in NEW `tests/test_workplace_preferences.py` | NEW `apps/api/work_routes.py`, NEW `apps/api/file_routes.py`, and NEW `apps/api/preview_routes.py`; opaque file metadata/content routes; W4-owned PreviewDrawer/RightPanel/FilePreview first create the control-plane one-time exchange then POST body-only proof with credentials to a same-site distinct-hostname `PREVIEW_ORIGIN`, and iframe the token-free preview route with `sandbox="allow-scripts allow-forms allow-same-origin"`. The transferred Vite seam uses preview-build `base: "./"`; transferred frontend global setup/config run a real two-host browser but no Python fixture runtime. Environment validation rejects an absent, same-hostname, or cross-site origin and keeps `workplace_preview_origin_v1` off. | Centralize one exchange helper and exact preflight/CORS/form-action allowlist. Run `uv run pytest tests/test_mission_projections.py tests/test_work_file_routes.py tests/test_workplace_preferences.py tests/test_preview_origin_routes.py tests/test_mission_api_routes.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck && npm --prefix apps/console run test:e2e -- preview-origin.spec.ts`. Evidence: no URL token, one-time exchange, preflight exactness, host-only cookie stays off the control host, nested module/assets resolve beneath the preview route, external form submission is CSP-denied, promoted-only bytes, exact CSP/CORS/control-origin framing, expiry/revocation/cross-tenant denial, and no deploy without a same-site distinct-hostname configured origin. After the W4 gate transfer `apps/api/main.py`/`tests/test_mission_api_routes.py` to W6B and environment/Vite/browser paths to W7. |
| W5 — threads, saved, SSE | After W3 transfer, `test_reply_depth_is_one`; `test_reaction_add_remove_is_idempotent`; `test_reaction_put_service_replays_and_rejects_hash_mismatch`; `test_reaction_delete_service_replays_and_rejects_hash_mismatch`; `test_saved_put_service_replays_and_rejects_hash_mismatch`; `test_saved_delete_service_replays_and_rejects_hash_mismatch` in NEW `tests/test_conversation_service.py`; `test_reaction_put_route_replays_and_rejects_hash_mismatch`; `test_reaction_delete_route_replays_and_rejects_hash_mismatch`; `test_saved_put_route_replays_and_rejects_hash_mismatch`; `test_saved_delete_route_replays_and_rejects_hash_mismatch` in transferred NEW `tests/test_conversation_api_routes.py`; `test_sse_resume_is_wakeup_only` in NEW `tests/test_workplace_event_routes.py`; `reconnect_fetches_durable_page_without_duplicate_timeline_rows` in NEW `apps/console/src/features/workplace/conversation/ConversationTimeline.test.tsx` | After W3 transfer, extend NEW `apps/api/conversation_routes.py` only for reply/reaction/save endpoints. Every reaction/save mutation accepts JSON `{client_request_id}`, assigns the exact route operation, and hashes method/route target/body under the full identity; NEW `apps/api/workplace_event_routes.py` owns `/workspace/events`; after W2 transfer, NEW `useWorkplaceQuery.ts` owns EventSource fallback; NEW `workplace/conversation/ThreadDrawer.tsx` renders it. | Extract reconciliation behind transferred `useWorkplaceQuery.ts`, cancel EventSource on unmount. Run `uv run pytest tests/test_conversation_service.py tests/test_conversation_api_routes.py tests/test_workplace_event_routes.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck`. Evidence: all four mutation methods replay only their full identity, reject target/body hash mismatches, `Last-Event-ID` resumes, and a dropped wake-up is repaired by durable GET; then transfer models/repository/service and EDIT `tests/test_collaboration_domain.py` ownership to W6. |
| W6 — invitations, member removal, external notification projection, presence, onboarding, and preferences (depends on W4 and W5) | In NEW `tests/test_invitation_acceptance.py`: `test_stolen_token_wrong_verified_email_is_unavailable`, `test_cross_tenant_token_use_is_unavailable`, `test_revoked_or_expired_invitation_is_unavailable`, `test_concurrent_accept_is_single_use_and_enrolls_once`, `test_accept_crash_before_and_after_each_identity_room_journal_boundary`, `test_concurrent_readers_never_observe_tenant_membership_without_room_membership`, `test_pending_transaction_rows_are_hidden_from_identity_and_room_readers`, `test_complete_transaction_rows_become_visible_after_restart`, `test_concurrent_room_mutation_preserves_hidden_pending_member_rows_while_authorized_read_filters_them`, and `test_pg_identity_with_filesystem_room_crash_recovery_is_complete_gated`; in EDIT `tests/test_identity.py`: `test_clerk_invitation_principal_rejects_invalid_signature`, `test_clerk_invitation_principal_rejects_expired_token`, `test_clerk_invitation_principal_rejects_missing_subject`, `test_clerk_invitation_principal_rejects_missing_trusted_email_proof_config`, and `test_clerk_provider_lookup_is_bound_to_verified_subject`; in EDIT `tests/test_db.py`: `test_user_verified_email_columns_are_expand_only`, `test_legacy_membership_defaults_to_committed_visibility`; in EDIT `tests/test_collaboration_domain.py`: `test_legacy_room_member_without_transaction_remains_visible`, `test_pending_invitation_revoke_is_idempotent`, `test_member_remove_owner_admin_only_and_keeps_last_owner`, `test_removed_member_loses_mission_work_attention_conversation_file_access`, `test_presence_threshold_boundaries_are_server_derived`, `test_presence_restart_returns_offline_without_authorization_effect`; in EDIT `tests/test_cmul8_api_routes.py`: `test_member_remove_route_requires_owner_admin_and_current_room_revision`, `test_invitation_accept_dependency_allows_pre_membership_but_returns_anti_enumeration_errors`, `test_presence_two_humans_are_scoped_and_membership_filtered`; in NEW `tests/test_notification_outbox.py`: `test_projector_repairs_event_before_outbox_and_outbox_before_cursor`, `test_two_process_projector_and_delivery_locks`, `test_notification_dead_letter_after_lease_retries`, `test_mute_suppresses_delivery_while_attention_remains_actionable`, `test_event_selection_projector_filter_matrix_and_orthogonal_mission_mute`, `test_provider_idempotency_key_and_crash_redelivery_contract`; in NEW `apps/console/src/features/workplace/crew/CrewRail.test.tsx`: `presence_heartbeat_stops_when_hidden_and_roster_renders_server_status`; in NEW `apps/console/src/features/workplace/onboarding/OnboardingChecklist.test.tsx`: `onboarding_checklist_has_no_runtime_copy`; in NEW `apps/console/src/features/workplace/onboarding/NotificationPreferences.test.tsx`: `notification_preference_event_selection_and_mute_preserve_attention` | Only after both W4 and W5 merge, using `cmul8_routes.py` and `tests/test_cmul8_api_routes.py` transferred from W1D, identity/security/persistence paths owned by W6, and models/repository/service plus EDIT `tests/test_collaboration_domain.py` transferred from W5. EDIT `simulacra/deploy_process.py`, `presence.py`, identity/security files, `cmul8_routes.py`, console API, ProjectRoomContainer, and TeamRoster; NEW acceptance/notification modules and nested onboarding components. Notification tests seed W4 preferences read-only and prove external filtering while attention remains authoritative. `NotificationPreferences.tsx` consumes the stable W4 preference endpoint/read contract and does not edit `preferences.py`, `preference_routes.py`, `apps/api/main.py`, or `tests/test_workplace_preferences.py`. At merge it transfers `deploy_process.py` to W6B only. | Run `uv run pytest tests/test_invitation_acceptance.py tests/test_identity.py tests/test_db.py tests/test_collaboration_domain.py tests/test_cmul8_api_routes.py tests/test_notification_outbox.py tests/test_workplace_preferences.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck`. Evidence: verified-email source, cross-tenant/expired/revoked/concurrent acceptance, every identity/room journal crash boundary and concurrent-reader invisibility, raw-pending-row preservation under ordinary concurrent room mutation, legacy visibility compatibility, pending-only revoke, access loss, presence, external outbox selection/mute with authoritative attention retained, and no W6 edit overlaps another lane. |
| W6B — workspace bootstrap and source ingestion (after W6 identity foundation) | In NEW `tests/test_workspace_bootstrap_coordinator.py`: `test_workspace_bootstrap_retry_returns_reserved_project_and_single_children`, `test_workspace_bootstrap_fault_injection_recovers_each_reservation_and_child_boundary`, `test_concurrent_workspace_bootstrap_requests_converge_on_one_reserved_project`, `test_bootstrap_status_is_actor_tenant_scoped_and_reports_provisioning_then_complete`, and `test_bootstrap_recovery_tick_resumes_durable_graph_build_intent_after_restart`; in NEW `tests/test_source_staging.py`: `test_staged_sources_are_tenant_actor_scoped_immutable_and_linked_to_reservation`, `test_workspace_bootstrap_staged_sources_replay_mismatch_and_crash_recovery`, and `test_blob_first_publication_never_exposes_missing_blob_and_gc_reclaims_orphan`; in EDIT `tests/test_operation_graph_contract.py`: `test_bootstrap_graph_build_result_is_restart_safe_and_scope_bound`; in NEW `tests/test_operation_graph_store.py`: `test_finalize_exact_revision_head_recovers_crash_after_revision_before_head`, `test_finalize_exact_revision_head_two_process_finalizers_converge`, `test_finalize_exact_revision_head_rejects_scope_or_hash_mismatch`, and `test_finalize_exact_revision_head_rejects_competing_valid_head`; in transferred EDIT `tests/test_mission_api_routes.py`: `test_post_projects_uses_current_tenant_bootstrap_reservation_and_rejects_client_tenant`, `test_project_upload_replays_by_client_request_id_and_rejects_hash_or_path_overwrite` | After the W4 and W6 transfers, NEW `simulacra/workplace/bootstrap_coordinator.py` journals tenant-scoped bootstrap reservation and durable graph-build intent/result under `.workplace-control/bootstrap-transactions`; NEW `simulacra/workplace/source_staging.py` owns pre-project blob-first immutable publication and bounded orphan GC; EDIT `apps/api/main.py` provides stage/status/project-upload seams and requires `{client_request_id, prompt, goal, design_brief, artifact_kind, staged_source_refs}` for bootstrap; EDIT `simulacra/demo/runs.py` accepts the reserved project ID and never generates a replacement; EDIT `simulacra/demo/operation_graph_builder.py` supplies the coordinator-only synchronous/recoverable graph-build entry point and EDIT `simulacra/operation_graph/store.py` owns locked exact-head finalization while `plan.py`/`jobs.py` remain unmodified; transferred EDIT `simulacra/deploy_process.py` runs only bounded W6B recovery. | Run `uv run pytest tests/test_workspace_bootstrap_coordinator.py tests/test_source_staging.py tests/test_operation_graph_contract.py tests/test_operation_graph_store.py tests/test_mission_api_routes.py -q`. Evidence: retry/concurrency always return one reserved project ID and one child set; pending status is caller-scoped; an in-memory `start_job` enqueue alone never completes; revision-before-head crash restarts through one locked finalization, concurrent finalizers converge, a competing valid head is a conflict never an overwrite/rollback, scope/hash mismatch never duplicates a revision, and graph-build/source matrices pass. |
| W7R — pinned Linux real-worker integration (earliest Day 11 / next execution window, after W6B gate) | `test_real_mission_worker_assignment_reaches_awaiting_verification` in NEW `tests/test_workplace_real_worker_integration.py` | Add only this NEW integration test; consume the public production MissionWorker/Codex transport boundary in the pinned Linux runtime image with deterministic local model transport and no external provider. A revealed worker defect is a separately owned post-coordinator-transfer follow-up, not W7R scope. | Run `uv run pytest tests/test_workplace_real_worker_integration.py -q`. Evidence: a journal-COMPLETE assignment reaches awaiting verification through the real worker boundary; this is not Playwright and does not assert live-provider coverage. |
| W7 — fixture-runtime browser proof and release rollout (earliest Day 11 / next execution window, after W6B gate) | In NEW `tests/test_workplace_fixture_runtime.py`: `test_fixture_runtime_rejects_external_or_provider_request`; in NEW `tests/test_workplace_rollout.py`: `test_rollout_environment_allows_internal_tenant_only`; in NEW `apps/console/src/features/workplace/visual/VisualScenarioBoards.test.tsx`: `visual_scenario_board_route_is_test_build_only`; in transferred NEW `apps/console/e2e/global-setup.ts` and NEW `apps/console/playwright.config.ts`; and in NEW `apps/console/e2e/workplace-loop.spec.ts`: `missions_needs_you_work_routes_preserve_state`, `two_humans_send_review_verify_without_refresh`, `lost_network_resend_is_exactly_once`, `keyboard_only_assignment_and_thread`, `mobile_core_loop_has_no_serious_axe_violation`, `first_workspace_onboarding_recovers_to_verified_output`, and `visual_scenario_matrix_has_32_baselines` | After W2's recorded transfer, EDIT `apps/console/src/App.tsx` mounts NEW `apps/console/src/features/workplace/visual/VisualScenarioBoards.tsx` at test-build-only `/__visual__/scenario/{1..8}`; after W4's recorded transfer, W7 creates the fixture runtime and consumes/extends global setup, Playwright config, and Vite seam; NEW rollout runbook; EDIT `deploy/environment-contract.json` and EDIT `deploy/environment.py` for release-owned allowlist only. | Run `uv run pytest tests/test_workplace_fixture_runtime.py tests/test_workplace_rollout.py -q && npm --prefix apps/console run test:unit && npm --prefix apps/console run typecheck && npm --prefix apps/console run test:e2e -- workplace-loop.spec.ts`. Evidence: fixture-runtime network denial, visual route unavailable outside test build, environment allowlist, onboarding recovery from first workspace to verified output, all named journeys, 32 composite-board visual-diff baselines, and separate journey screenshots; no earlier lane activates a flag. |

W6 auth RED addendum: EDIT `tests/test_identity.py` must write `test_clerk_invitation_principal_rejects_invalid_signature`, `test_clerk_invitation_principal_rejects_expired_token`, `test_clerk_invitation_principal_rejects_missing_subject`, `test_clerk_invitation_principal_rejects_missing_trusted_email_proof_config`, and `test_clerk_provider_lookup_is_bound_to_verified_subject`, in addition to the issuer/audience, unverified/fallback email, subject mismatch, and production-local-path tests; NEW `tests/test_invitation_acceptance.py` must write the pending-row reader/restart and PG-filesystem COMPLETE-gate tests. These tests use the W6 command already listed above and do not make network calls.

### 13.7 Per-wave acceptance evidence and rollback gates

| Wave | Acceptance evidence retained with PR | Rollback / compatibility gate |
| --- | --- | --- |
| W1 | JSON fixtures, public field denylist output, single-state edit/delete audit crash matrix, coordinator fault-injection matrix, concurrent-reader/worker-claim convergence log, and lock-order assertion. | Flags default off; all journal states other than COMPLETE are invisible/non-claimable; post-decision recovery completes without rollback. |
| W2 | Three-Mission membership screenshot, URL/filter reload test, attention read receipt before/after JSON. | `workplace_shell_v1` off returns existing console routing; attention source events unaffected. |
| W3 | One request/retry trace with identical transaction/message/work IDs, graph-unapproved no-PREPARED-write proof, PREPARED/STORES_DURABLE/queued invisibility, COMPLETE-only publication/claim, ABORTED replay, and multi-agent handoff test. | `workplace_conversation_v1` off retains existing comments/events and direct-run route; COMPLETE messages remain durable/readable. |
| W4 | File response headers, hash-mismatch rejection, staged-code no-preview proof, same-site distinct-hostname preview environment/one-time exchange/token-free nested asset/preflight/CORS/CSP/frame/no-sniff/private-cache/real-browser host-only-cookie evidence, provenance deep-link screenshot, WorkList saved-view/filter restoration, preference CAS/restart/crash matrix, and current-human/nonmember-mute route evidence. | `workplace_files_v1` and `workplace_preview_origin_v1` default off; no deployed preview is enabled unless the same-site distinct-hostname origin passes environment validation. Flag-off hides the new viewer and never removes source/output data or verification evidence. |
| W5 | SSE reconnect durable-refresh test plus reaction/private-save tests. | SSE can be disabled to polling; durable reads remain authoritative. |
| W6 (after W4 and W5) | Invitation verified-email/token/cross-tenant/expiry/revoke/concurrent-accept plus identity/room-journal crash, pending-row authorization filter, legacy-record compatibility, and concurrent-reader evidence; pending-invite revoke/member-removal authority/no-leak evidence; server-derived presence threshold/restart/two-human UI evidence; external projector replay across event/outbox/cursor boundaries; two-process projector/delivery locks; provider-key/lease redelivery contract; event-selection/mute matrix; and component/service onboarding evidence only. | Stop projector/external-delivery ticks before rollback; outbox replay never creates source actions, accepted membership remains compatible with legacy and COMPLETE-gated room readers, and transport retry remains explicitly at-least-once where a provider cannot deduplicate. |
| W6B | Bootstrap reservation/blob-first source-publication/journal crash matrix, bounded recovery-tick log, restart-safe graph-build-intent/durable-revision proof, actor/tenant-scoped status trace, two-process retry convergence, one-project/one-Mission/one-room/one-graph evidence, immutable source adoption/project-upload replay evidence, and `POST /projects` current-tenant/no-client-tenant response trace. | `workplace_bootstrap_v1` off returns the legacy create route only until all callers migrate; rollback preserves staged-source/reservation journals and completed project state. |
| W7 | Pinned-Linux real-worker integration result, fixture-runtime network-denial log, test-build-only visual-route proof, rollout-environment allowlist test, named Playwright journey reports including first-workspace onboarding recovery, 32-composite-board visual baseline matrix, separate journey screenshot evidence, manual unfamiliar-user checklist, and rollout runbook drill. | Release lane turns flags off first, verifies legacy reads, then rolls back. No schema contract/collection deletion is part of rollback. |

### 13.8 First ten working days

The schedule assumes four parallel lanes after the reviewed baseline. A day ends only when its listed artifact and gate are available; unfinished work carries forward rather than silently compressing tests.

| Day | Contract/domain lane | API/security lane | Console lane | QA/release lane | End-of-day deliverable |
| --- | --- | --- | --- | --- | --- |
| 1 | Record baseline commit; freeze DTO fixture shapes | Audit current serializers/routes | Audit import/navigation plus nested workplace migration seams | Test-assets lane freezes fixtures/fakes; visual lane freezes design harness inputs | Clean reviewed baseline and ownership handoff. |
| 2 | Pair on W0 deterministic harness and coordinator fixture utilities; merge GREEN together | Define public field/flag test fixtures | Pair on fake clock/EventSource, design token harness renderer, and local two-origin browser smoke | Verify only that the two local frontend origins start; reserve Python fixture/network denial for W7 | W0 one-PR GREEN harness, test-assets lane, visual-foundation harness, and W0→W4 browser-path transfer merged; no RED-only merge. |
| 3 | RED/GREEN W1A Mission models/repository/service/projections plus independent W1B audit collection | GREEN flag resolver skeleton after fixture contract | Begin shell contracts only | Verify deterministic IDs/timestamps | W1A/W1B persistence slices merged; record W1A Mission-path transfer to W1C and W1A projection/test transfer to W2. |
| 4 | RED/GREEN W1C coordinator fault boundaries only after recorded W1A transfer | Wire coordinator-aware serializers/error envelope | Shell + attention route state | Run `tmp_path` and two-process convergence tests | Coordinator protocol merged before assignment UI. |
| 5 | Support W1D API/flag contracts | GREEN aggregate routes/authorization/tenant flags; record the W1D→W4 EDIT `apps/api/main.py` and EDIT `tests/test_mission_api_routes.py` transfers | Build Missions/Needs you list/inbox | Run regression subset | W1D public API and W2 shell vertical slice merged; W2 transfers NEW `simulacra/missions/projections.py` and NEW `tests/test_mission_projections.py` exclusively to W4, while `apps/api/main.py` and `tests/test_mission_api_routes.py` move to W4 for preference routing and preview security; invitation work remains reserved for W6 after W5. |
| 6 | RED/GREEN W3 idempotency, PREPARED through COMPLETE, ABORTED, ordering, and legacy-submission-removal tests after W2 AgentShell transfer | Delegate assignment endpoint to coordinator | Build nested workplace/conversation components | Freeze fixture-runtime/two-context specifications only; W7 implementation has not begun | Recoverable message/assignment slice works locally. |
| 7 | GREEN W3 recovery/refactor; crash-atomic edit/delete audit proof | Add durable GET/PATCH/DELETE conversation projection | Complete composer retry/error states and transferred AgentShell legacy-submit removal | Verify replay trace and banned-field scan | W3 merged; direct legacy composer call removed. |
| 8 | RED/GREEN transferred work projection, opaque-file security, same-site distinct-hostname preview exchange/preflight/header/CORS/CSP form-action/sandbox real-browser tests, and preference repository crash/CAS tests | After the recorded W1D, W0, and W2 transfers, mount preference routes, implement the preview-origin exchange/router, relative Vite build base, environment validation, and work/file endpoints | Work list/Files hierarchy plus legacy PreviewDrawer/RightPanel and new FilePreview exchange flow | Run preview-origin nested-asset/preflight/host-only-cookie/external-form-denial browser proof plus preference restart/crash assertions | W4 backend/API, preview-origin security, and preference lane merged; transfer `main.py`/`test_mission_api_routes.py` to W6B and environment/Vite/browser paths to W7; invitation/member work remains reserved until both W4 and W5 merge. |
| 9 | Complete W5 thread/SSE and release collaboration ownership | SSE and durable refresh | Thread drawer plus Crew/Work/Files nested migrations | Audit W7 fixture/browser requirements only; do not implement or run W7 | W5 merged; models/repository/service and EDIT `tests/test_collaboration_domain.py` ownership transfer to W6; W4+W5 prerequisite is now satisfied. |
| 10 | Complete W6 invitation-accept/member-removal/projector/presence crash and threshold tests after W4+W5 | Complete invitation/member/presence routes, projector/delivery ticks, and the W6-owned `cmul8` route/API tests | Complete onboarding/preferences/presence surfaces plus keyboard/mobile W6 polish | Run the complete W6 command and retain its invitation, presence, authorization, outbox, preference, and no-overlap evidence | Ten-day plan ends at a W6-ready baseline: W6 is merged and its full gate passes; W7R/W7 have not begun. |

### 13.9 Final quality gate checklist

Before enabling any flag beyond an internal tenant, the release owner records each result explicitly:

- For tracked changes, `git diff --check -- docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md` is clean. For this untracked plan, `git diff --no-index --check /dev/null docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md` may exit 1 only because it is an added file; any whitespace diagnostic fails the gate.
- `uv run pytest -q` passes with no network/model dependency.
- `npm --prefix apps/console run test:unit`, `npm --prefix apps/console run typecheck`, and `npm --prefix apps/console run build` pass.
- Deterministic Playwright passes in two isolated browser contexts and proves UI/multiplayer/retry/accessibility without a provider/network; the resend path produces exactly one message and one assignment/run.
- `uv run pytest tests/test_workplace_real_worker_integration.py -q` passes in the pinned Linux runtime image through the production MissionWorker/Codex transport boundary with deterministic local model transport; it does not claim live-provider coverage.
- Real-filesystem fault-injection covers every coordinator journal/store replace and fsync boundary; concurrent projection/claim tests prove every state except COMPLETE is invisible/non-claimable, including queued-before-COMPLETE; post-decision recovery reaches COMPLETE without rollback.
- `uv run pytest tests/test_workplace_preferences.py -q` proves current-human-only preference routes, aggregate CAS/restart persistence, descriptor-safe crash-atomic replacement, membership-validated mutes, and atomic `event_selection` updates; W6's `NotificationPreferences.test.tsx` proves it consumes that endpoint contract without editing W4-owned files.
- Pending-invite revoke and separate member removal remove Mission/work/attention/conversation/file aggregate access immediately; Search revocation remains the later WP-13 scope.
- Server public serializer denylist checks prove absence of runtime/model/provider/credential/path/raw-tool fields.
- A staged code output cannot be previewed or promoted without exact verified hash and authorized human action.
- Preview is unavailable unless a validated `PREVIEW_ORIGIN` has a same-site, distinct hostname from control and `workplace_preview_origin_v1` is internally enabled. A control-plane one-time body-only exchange gives the preview origin a host-only capability cookie; exact OPTIONS preflight/POST CORS permits only the configured control origin, and real-browser proof shows the cookie is not sent to the control host. Token-free nested assets use Vite `base: "./"` and run in a sandboxed iframe with `allow-same-origin` only because the preview is a separate untrusted origin. CSP permits only self scripts/styles, `form-action 'self'`, and fixture-proven `data:` image/font sources, never broad `unsafe-inline`; W4 retains exact external-form-submission denial, `frame-ancestors`, membership revocation, cross-tenant, exchange replay, staging, and guessed-ID evidence. Cross-site preview and Storage Access requests remain unsupported with the flag off.
- Bootstrap source bytes are blob-first immutable tenant/actor-scoped staged refs before reservation or project-scoped uploads after reservation; orphan blobs remain invisible until bounded GC, no record can reference a missing blob, `GET /projects/bootstrap/{transaction_id}` is actor/tenant scoped, and the bounded W6B recovery tick cannot mark COMPLETE until the journal-backed graph-build intent has produced the exact durable approved or ready-for-approval revision/head.
- All eight Section 6.15 scenarios are test-build-only composite boards at `/__visual__/scenario/{1..8}`, each captured at 1440×1000, 1180×820, 834×1112, and 390×844: exactly 32 visual-diff baselines with settled composition and no internal terminology. Named Playwright journeys separately prove navigation, scroll ownership, overlays, keyboard routes, and interactions; their screenshots are evidence only and are not counted in the 32.
- Flag-off reads and the release-owned documented rollback drill preserve conversation, work, trajectory, evidence, and verified outputs.
- Invitation acceptance/pending revoke/member removal, server-derived presence, projector replay across event/outbox/cursor boundaries, leased notification delivery/dead-letter/provider-key redelivery contract, preference selection/mute, fixture-runtime provider/network denial, and the internal-only rollout environment all have recorded evidence.

The requested first ten working days intentionally end at the W6-ready baseline. W6B bootstrap begins no earlier than Day 11 after the W6 identity foundation, and W7R/W7 begin only after the complete W6B gate has passed; browser/visual/manual/release completion is not a Day 10 claim.

No launch waiver can substitute for a failed authorization, idempotency, verification, privacy, or rollback gate.

---

## 14. Codex-native build and runtime orchestration

This section incorporates current official Codex workflow guidance into the implementation protocol. It distinguishes two systems that must not be conflated:

1. **Build orchestration** — how Codex agents implement and verify this repository.
2. **Product orchestration** — how customer-visible Mission agents coordinate durable work.

Official sources:

- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
- [Codex code review](https://learn.chatgpt.com/docs/code-review)
- [Codex long-running work](https://learn.chatgpt.com/docs/long-running-work)
- [Codex SDK](https://learn.chatgpt.com/docs/codex/codex-sdk)
- [Codex as a platform](https://learn.chatgpt.com/blog/codex-as-a-platform)
- [Automating repetitive work at OpenAI with Codex](https://learn.chatgpt.com/blog/automating-repetitive-work-at-openai-with-codex)
- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)

### 14.1 Decisions adopted from the official guidance

- The primary agent remains the architect and preserves requirements, contract decisions, integration state, and final acceptance. Subagents return distilled results instead of flooding the primary context with raw logs.
- Parallel work is preferred for read-heavy exploration, documentation verification, browser reproduction, test diagnosis, and other independent work. Write-heavy work is parallel only when the ownership matrix proves disjoint files and interfaces.
- Every long-running work packet is outcome-first: **outcome, constraints, verification, evidence, stopping rule**. Process instructions are included only when the exact path is part of the product or security contract.
- Repository rules are durable in root `AGENTS.md`; nested overrides may narrow commands or ownership for a subtree but may not weaken the public product, security, or verification contract.
- Independent writers use isolated worktrees or strictly disjoint file ownership. Two agents never receive write access to the same source, fixture, generated lockfile, route aggregator, schema union, or test baseline at the same time.
- High-risk changes receive a fresh read-only diff review after parent integration and verification. The reviewer reports prioritized correctness, security, behavior, and missing-test findings and does not modify the tree.
- Prompts remain lean: state each policy once, expose only relevant tools, use typed structures for machine contracts, and validate every tool result server-side.

### 14.2 Build-orchestration state machine

Each W0–W7 wave follows this state machine:

```text
SCOPED
  -> RED_PROVEN
  -> IMPLEMENTING
  -> FOCUSED_GREEN
  -> INTEGRATED
  -> FULL_VERIFIED
  -> REVIEWED
  -> ACCEPTED
```

Rules:

- `SCOPED`: the primary records baseline commit, dirty paths, owned files, exclusions, frozen interfaces, expected tests, and rollback boundary.
- `RED_PROVEN`: named tests fail for the intended missing behavior; an infrastructure or fixture failure is not a valid RED.
- `IMPLEMENTING`: exactly one writer owns each path. Parallel agents may read shared files but cannot mutate them.
- `FOCUSED_GREEN`: the work packet's exact command passes and the agent returns changed files plus evidence.
- `INTEGRATED`: the primary inspects the accumulated diff and resolves interface transfers; agent summaries are claims, not acceptance evidence.
- `FULL_VERIFIED`: applicable Python, console, browser, accessibility, security, and diff checks pass from the integrated tree.
- `REVIEWED`: required fresh read-only review returns `ship`; any fix invalidates the prior verdict and requires re-verification and a new review.
- `ACCEPTED`: the wave evidence and ownership transfers are recorded. Only then may a dependent wave begin.

No lane may skip from `IMPLEMENTING` to `ACCEPTED`. A partial implementation can remain in its current state but is never described as complete.

### 14.3 Standard agent topology for implementation

Use no more agents than the task can keep genuinely independent:

| Role | Typical work | Write policy | Required return |
| --- | --- | --- | --- |
| Primary architect | Requirement resolution, contracts, ownership, integration, final commands | May edit only work it retains; never duplicates a delegated implementation | Accepted diff, evidence, residual risk |
| Explorer | Trace code paths, existing contracts, migrations, and likely test seams | Read-only | Files/symbols, execution path, uncertainties |
| Implementer | Complete one bounded vertical slice from RED to GREEN | Exact exclusive paths | Changed files, tests/results, contract deviations, residual risk |
| Browser verifier | Reproduce and validate a UI journey at required viewports | Read-only product inspection; screenshot evidence only unless separately assigned test files | Steps, screenshots, console/network evidence, accessibility findings |
| Fresh reviewer | Inspect the integrated diff for P0/P1 correctness, security, concurrency, privacy, and test gaps | Strictly read-only | `ship`, `fix-first`, or `rethink` with file references |

Default topology is primary-only. Add an explorer when codebase uncertainty is material, an implementer when a bounded lane can substitute for primary work, and a fresh reviewer when risk warrants it. Do not create agents merely to make the activity panel look busy.

### 14.4 Work packet template

Every delegated packet must be concrete enough to execute without rediscovering product intent:

```text
OBJECTIVE
<one measurable vertical result>

FILES AND OWNERSHIP
EDIT: <exact paths>
NEW: <exact paths>
DO NOT EDIT: <shared/transferred paths>
You are not alone in the repository. Preserve concurrent changes.

INTERFACES
<request/response schemas, state transitions, events, identifiers, feature flags>

CONSTRAINTS
<authorization, privacy, idempotency, crash safety, UI copy, accessibility,
responsive behavior, hidden infrastructure, compatibility and rollback>

VERIFICATION
RED: <exact named tests and intended failure>
GREEN: <focused command>
REGRESSION: <applicable broader command>
EVIDENCE: <screenshots, traces, hashes, headers or logs>

RETURN
Changed files; tests and exact results; behavioral summary; remaining risks;
anything that requires a primary contract decision.
```

Follow-up instructions steer the same agent only while its ownership remains unchanged. A materially different objective gets a fresh packet and, when writing, a new ownership decision.

### 14.5 Parallel execution map for this plan

The existing dependency DAG remains authoritative. Codex orchestration applies it as follows:

- **W0:** parallel read-only audits of frontend test seams, deterministic fixtures, and visual tokens; one package/lockfile writer integrates dependency changes.
- **W1:** W1A contract/domain and W1B conversation persistence may run independently after fixtures freeze. W1C begins only after the recorded W1A transfer. W1D consumes frozen public unions and owns the route envelope.
- **W2:** shell/UI implementation may proceed alongside read-only API-contract verification; no agent may edit transferred projection or navigation paths outside the recorded owner.
- **W3:** one vertical-slice implementer owns message-to-assignment-to-run behavior. Browser verification may run only after focused GREEN and remains read-only.
- **W4:** preview security, preferences, and work/file projection can be split only along the exact ownership matrix. Browser security proof begins after the server exchange contract is GREEN.
- **W5:** SSE/reconciliation and conversation extensions remain one ownership lane because they share durability and replay semantics.
- **W6:** invitation/identity, notification outbox, and UI onboarding may have independent explorers, but one implementer owns each shared persistence or route path according to the matrix.
- **W6B:** bootstrap reservation and graph finalization are one coordinator lane; fault-injection analysis may run in parallel, implementation may not.
- **W7:** real-worker integration, deterministic browser journeys, and visual/accessibility capture are independent after the W6B gate. Release activation remains a single owner and runs last.

### 14.6 Product-execution orchestration contract

Missions uses a versioned `MissionAgentExecutor` boundary and ships Codex app-server as its first built-in executor, but **Missions owns the product orchestration**.

- The executor is deployment configuration, never an end-user or tenant choice. A non-default executor must be reviewed and baked into the deployment image's source-controlled certified registry, implement the versioned boundary, match the backend pinned into the admitted run, use managed isolation, and fail readiness closed when unavailable. Environment input may select a certified entry but may not import arbitrary application-process code.
- Use the Codex app-server executor by default because the product requires persistent threads, streamed events, interruption, tools, and approval handling. Keep it behind the executor and transport adapters so another certified harness or a later SDK migration does not change Mission APIs or durable records.
- One customer-visible Mission agent maps to one durable Mission identity and one resumable executor session lineage. Display names and roles come from Mission records, never from executor session metadata.
- Every certified executor consumes the same immutable admission snapshot and returns the same normalized result/evidence contract. The adapter API receives the admitted request, managed isolation, and its execution-session repository—not the Mission service or repository. Switching executors cannot bypass scope, budget, approval, artifact, or verification rules, and returned backend/provider/model identity must match admission before results can become Mission evidence.
- A non-Codex executor uses the `mission-executor-json-v1` process contract from its own root-owned `/opt/cmul8/executors/<backend>` runtime. The trusted launcher supplies the same request-bound filesystem sandbox and secret allowlist and starts exactly `mission-executor --stdio`; before each action the child must emit an `action_request` and wait for the matching `action_admitted` response. The supervisor withholds that response at the action ceiling, stops the process at its wall-time or output ceiling, accepts one normalized result whose usage equals admitted actions, and fails closed on malformed output. Because the trusted adapter must reach the selected model while model-invoked tools must remain offline, certification requires the baked runtime to implement and declare that internal network separation; discovery/readiness rejects an adapter that does not. No alternate executor inherits Codex state, paths, profiles, or transport arguments.
- Model routing is independent from executor selection. The built-in Codex executor accepts the official OpenAI route or an operator-owned, credential-free HTTPS base URL implementing the Responses contract; model IDs and routes are pinned into the run while credential values are never persisted.
- Multi-agent collaboration is coordinated by durable Mission assignments, dependencies, handoffs, permissions, checkpoints, and evidence. A model may recommend delegation, but only the application coordinator can create a new customer-visible assignment or start another agent.
- Do **not** enable invisible nested Codex subagents inside a customer Mission in V0. They would bypass stable identity, role/scope enforcement, human routing, cost attribution, durable progress, and evidence provenance. Reconsider only after every nested agent event, tool action, approval, artifact, and failure can be projected one-to-one into the Mission record.
- The Mission database and immutable artifacts are authoritative. Executor session state is resumable execution context; streamed deltas are transient UI progress and cannot independently resolve work, permission, or verification state.
- Each run receives only its role-relevant sources and tools. Server-side allowed-tool selection enforces the effective capability set; prompt text alone never grants authority.
- A long-running agent run binds outcome, definition of done, role, source versions, permission scope, effective budget, approved graph revision/hash, and evidence requirements before launch.
- Consequential choices pause at a durable human checkpoint. Cancellation, replacement, retry, and lease recovery must interrupt or fence the active runtime before another writer can mutate the same Mission scope.
- After a run, preserve a concise decision/evidence record: intent, relevant inputs, actions, approvals, outputs, validation, failures, and preferred next-run guidance. Secret-safe trajectory export is derived from this record, not raw runtime transcripts.

### 14.7 Orchestration UX consequences

The runtime architecture must make the workplace simpler, not expose more machinery:

- Conversation shows meaningful states such as `Investigating sources`, `Draft ready`, `Waiting for Priya`, or `Verification failed`; never subagent/thread/process names.
- Crew shows durable humans and Mission agents only. Internal explorers, reviewers, retries, and runtime children do not appear as additional teammates.
- Work shows one human-understandable item per committed assignment, with substeps and handoffs inside its activity timeline rather than duplicated cards.
- Needs You is created from durable approval, question, failure-recovery, and verification records—not from parsing model prose.
- Files groups source, working, evidence, and output versions with their responsible Mission agent and human verifier.
- The primary visible progress sentence is derived from durable state and remains correct after refresh, reconnect, worker restart, or model-thread compaction.

### 14.8 Orchestration reliability tests

Add these tests to the relevant existing waves rather than creating a separate generic orchestration subsystem:

- A multi-agent handoff produces one committed parent work item, ordered child assignments, stable agent IDs, and no duplicate run after response loss/retry.
- Two independent read-only agents may run concurrently; two write-capable agents targeting the same Mission scope cannot acquire live leases concurrently.
- A model-proposed handoff cannot create an assignment until server authorization, graph revision, role scope, and idempotency validation pass.
- Runtime thread resume restores context after process restart without treating runtime messages as committed conversation or evidence.
- A hidden runtime/subagent event cannot appear in public Crew, Conversation, Work, Files, Needs You, notification, or trajectory payloads.
- Interrupt/replacement fences all prior writers before a successor run starts.
- Compaction or thread replacement preserves the same bound Mission outcome, source revisions, permission scope, budget, and definition of done.
- Agent output, tool results, and errors cannot resolve verification automatically; only an authorized human decision over the exact candidate hash can publish.
- The browser shows one coherent progress sequence across refresh and reconnect, with no duplicate agent, task, output, or notification rows.
- A fresh read-only reviewer can reproduce every release claim from the integrated diff and retained test/browser evidence without relying on implementer narration.

### 14.9 First implementation invocation

The next coding invocation begins with W0 only:

1. Record the current dirty baseline and assign every existing change to preserve, commit, or explicitly carry.
2. Freeze W0 outcome, exact paths, five named RED tests, dependency versions, and expected screenshots.
3. Run read-only exploration of the console test/build seams and design-system imports in parallel.
4. Give one implementer exclusive ownership of the package manifest and lockfile plus the W0 harness paths.
5. Integrate in the primary session and run W0 unit, typecheck, build, browser-smoke, and diff gates.
6. Obtain fresh read-only review of dependency, harness determinism, accessibility, and accidental product-behavior changes.
7. Accept W0 and record its path transfers before opening W1 lanes.

This ordering uses Codex parallelism where it improves discovery and verification while preserving the single-writer and durable-contract guarantees required by Missions.
