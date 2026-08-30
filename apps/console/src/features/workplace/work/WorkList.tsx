import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  ApiError,
  claimCmul8Task,
  decideMissionCheckpoint,
  getWorkspacePreferences,
  listMissionSummaries,
  listWorkspaceWork,
  putWorkViewPreference,
  retryMissionRun,
  reviewCmul8Task,
  transitionCmul8Task,
  type MissionSummary,
  type WorkActionTarget,
  type WorkBucket,
  type WorkItem,
  type WorkView,
  type WorkViewFilters,
  type WorkViewPreference,
} from "../../../api";
import "./work.css";

const buckets: Array<{ id: WorkBucket; label: string }> = [
  { id: "needs_you", label: "Needs you" },
  { id: "in_progress", label: "In progress" },
  { id: "ready_for_review", label: "Ready for review" },
  { id: "done", label: "Done" },
  { id: "stopped", label: "Stopped" },
];

const actionLabels: Record<string, string> = {
  open: "Open details",
  claim_work: "Take ownership",
  update_work: "Update",
  review_work: "Review",
  decide_checkpoint: "Review decision",
  verify_output: "Review evidence",
  retry_work: "Review restart",
  review_plan: "Review plan",
};

const actionPriority = [
  "decide_checkpoint",
  "verify_output",
  "retry_work",
  "review_plan",
  "review_work",
  "update_work",
  "claim_work",
  "open",
] as const;

function isAccessLoss(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function stateLabel(value: WorkBucket): string {
  return buckets.find((bucket) => bucket.id === value)?.label || "In progress";
}

function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Recently updated";
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "Updated now";
  if (minutes < 60) return `Updated ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Updated ${hours}h ago`;
  return `Updated ${Math.round(hours / 24)}d ago`;
}

function publicPrimaryAction(item: WorkItem): { id: string; label: string } | null {
  for (const id of actionPriority) {
    if (!item.allowed_actions.includes(id)) continue;
    if (actionLabels[id]) return { id, label: actionLabels[id] };
  }
  return null;
}

function preferenceFor(preferences: WorkViewPreference[], scope: string): WorkViewPreference {
  return preferences.find((preference) => preference.scope === scope) || {
    scope,
    view: "list",
    filters: {},
    revision: 0,
    updated_at: null,
  };
}

function itemKey(item: WorkItem): string {
  return `${item.source_type}:${item.source_id}`;
}

type SelectedWork = { item: WorkItem; opener: HTMLElement | null };
type WorkMutationInput = { decision?: "approve" | "request_changes" | "reject"; note?: string; state?: string };
type WorkMutationOutcome = { kind: "success" | "error"; message: string };

function trapDialogFocus(event: KeyboardEvent<HTMLElement>, onClose: () => void) {
  if (event.key === "Escape") {
    event.preventDefault();
    onClose();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function matchingTarget(item: WorkItem, action: string): WorkActionTarget | null {
  const target = item.action_targets?.[action];
  if (!target || typeof target.id !== "string" || !target.id || !Number.isInteger(target.revision)) return null;
  const expected: Record<string, WorkActionTarget["kind"]> = {
    claim_work: "task",
    update_work: "task",
    review_work: "task",
    decide_checkpoint: "approval",
    verify_output: "output",
    retry_work: "run",
    review_plan: "plan",
  };
  if (expected[action] && expected[action] !== target.kind) return null;
  if (action === "verify_output" && (typeof target.file_id !== "string" || !target.file_id)) return null;
  return target;
}

function nextTaskStates(target: WorkActionTarget | null): Array<{ value: string; label: string }> {
  const labels: Record<string, string> = {
    proposed: "Propose work",
    ready: "Return to queue",
    working: "Start work",
    in_review: "Send for review",
    done: "Complete work",
    blocked: "Mark blocked",
    failed: "Stop as unsuccessful",
    cancelled: "Cancel work",
  };
  const values = Array.isArray(target?.next_states)
    ? [...new Set(target.next_states.filter((value): value is string => typeof value === "string" && Boolean(value)))]
    : [];
  return values.map((value) => ({ value, label: labels[value] || "Update work" }));
}

function WorkDetail({ selected, missionName, focusAction, onSubmit, onReviewPlan, onReviewOutput, onClose }: {
  selected: SelectedWork;
  missionName: string;
  focusAction?: string | null;
  onSubmit: (action: string, input?: WorkMutationInput) => Promise<WorkMutationOutcome>;
  onReviewPlan: () => void;
  onReviewOutput: () => void;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const actionRef = useRef<HTMLButtonElement>(null);
  const preferred = focusAction && selected.item.allowed_actions.includes(focusAction) && actionLabels[focusAction]
    ? { id: focusAction, label: actionLabels[focusAction] }
    : null;
  const action = preferred || publicPrimaryAction(selected.item);
  const target = action ? matchingTarget(selected.item, action.id) : null;
  const transitions = nextTaskStates(target);
  const [nextState, setNextState] = useState(transitions[0]?.value || "");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<WorkMutationOutcome | null>(null);

  useEffect(() => {
    setNextState((current) => transitions.some((transition) => transition.value === current)
      ? current
      : transitions[0]?.value || "");
  }, [selected.item.source_id, target?.revision, target?.next_states?.join("\u0000")]);

  const submit = async (input?: WorkMutationInput) => {
    if (!action || !target || pending) return;
    setPending(true);
    setFeedback(null);
    const outcome = await onSubmit(action.id, input);
    setFeedback(outcome);
    setPending(false);
  };

  useEffect(() => {
    const target = focusAction === action?.id ? actionRef.current : closeRef.current;
    target?.focus();
    return () => selected.opener?.focus();
  }, [action?.id, focusAction, selected.item.source_id, selected.opener]);
  return <aside className="work-detail" role="dialog" aria-modal="true" aria-label="Work details" onKeyDown={(event) => trapDialogFocus(event, onClose)}>
    <header><div><p className="workplace-eyebrow">{stateLabel(selected.item.state)}</p><h3>{selected.item.title}</h3></div><button ref={closeRef} type="button" aria-label="Close Work details" onClick={onClose}>Close</button></header>
    <p>{selected.item.summary}</p>
    <dl><div><dt>Mission</dt><dd>{missionName}</dd></div><div><dt>Owner</dt><dd>{selected.item.assignee?.display_name || "Unassigned"}</dd></div><div><dt>Updated</dt><dd>{relativeTime(selected.item.updated_at)}</dd></div></dl>
    {action && action.id !== "open" ? <section className="work-action-panel" aria-label={action.label}>
      <h4>{action.label}</h4>
      {!target ? <p role="alert">This action is no longer available. Refresh Work to continue safely.</p> : action.id === "claim_work" ? <button ref={actionRef} className="work-detail-action" disabled={pending} type="button" onClick={() => void submit()}>{pending ? "Taking ownership…" : "Take ownership"}</button>
        : action.id === "update_work" ? <>
          {transitions.length ? <><label>Next state<select aria-label="Next state" value={nextState} disabled={pending} onChange={(event) => setNextState(event.target.value)}>{transitions.map((transition) => <option key={transition.value} value={transition.value}>{transition.label}</option>)}</select></label>
          <button ref={actionRef} className="work-detail-action" disabled={pending || !nextState} type="button" onClick={() => void submit({ state: nextState })}>{pending ? "Saving update…" : transitions.find((transition) => transition.value === nextState)?.label || "Save update"}</button></> : <p role="alert">This Work has no available status change. Refresh Work to continue safely.</p>}
        </> : action.id === "review_work" ? <>
          <label>Review note<textarea aria-label="Review note" value={note} disabled={pending} onChange={(event) => setNote(event.target.value)} placeholder="What did you verify, or what needs to change?" /></label>
          <div className="work-action-buttons"><button ref={actionRef} className="work-detail-action" disabled={pending} type="button" onClick={() => void submit({ decision: "approve", note })}>Approve work</button><button disabled={pending || !note.trim()} type="button" onClick={() => void submit({ decision: "request_changes", note })}>Request changes</button><button disabled={pending || !note.trim()} type="button" onClick={() => void submit({ decision: "reject", note })}>Reject work</button></div>
        </> : action.id === "decide_checkpoint" ? <div className="work-action-buttons"><button ref={actionRef} className="work-detail-action" disabled={pending} type="button" onClick={() => void submit({ decision: "approve" })}>Approve and continue</button><button disabled={pending} type="button" onClick={() => void submit({ decision: "reject" })}>Reject decision</button></div>
          : action.id === "verify_output" ? <button ref={actionRef} className="work-detail-action" type="button" onClick={onReviewOutput}>Open output and evidence</button>
            : action.id === "retry_work" ? <button ref={actionRef} className="work-detail-action" disabled={pending} type="button" onClick={() => void submit()}>Retry work</button>
              : action.id === "review_plan" ? <button ref={actionRef} className="work-detail-action" type="button" onClick={onReviewPlan}>Review plan</button> : null}
      {feedback ? <p className={`work-action-feedback is-${feedback.kind}`} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.message}</p> : pending ? <p className="work-action-feedback" role="status">Saving this change…</p> : null}
    </section> : null}
  </aside>;
}

export function WorkList({ missionId, focusItemId = null, focusAction = null, onAccessLost }: {
  missionId?: string;
  focusItemId?: string | null;
  focusAction?: string | null;
  onAccessLost?: () => void;
}) {
  const scope = missionId ? `mission:${missionId}` : "workspace";
  const [preference, setPreference] = useState<WorkViewPreference | null>(null);
  const [items, setItems] = useState<WorkItem[]>([]);
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selected, setSelected] = useState<SelectedWork | null>(null);
  const [accessLost, setAccessLost] = useState(false);
  const loadSequence = useRef(0);

  const clearForAccessLoss = useCallback(() => {
    loadSequence.current += 1;
    setItems([]);
    setSelected(null);
    setLoading(false);
    setLoadingMore(false);
    setAccessLost(true);
    onAccessLost?.();
  }, [onAccessLost]);

  const loadPreferences = useCallback(async () => {
    try {
      const result = await getWorkspacePreferences();
      setPreference(preferenceFor(result.work_view_preferences, scope));
      return result;
    } catch (requestError) {
      if (isAccessLoss(requestError)) {
        setPreference(preferenceFor([], scope));
        clearForAccessLoss();
        return null;
      }
      setPreference(preferenceFor([], scope));
      setNotice("Your saved view is unavailable. The default view is open.");
      return null;
    }
  }, [clearForAccessLoss, scope]);

  useEffect(() => {
    setItems([]);
    setSelected(null);
    setPreference(null);
    setAccessLost(false);
    void loadPreferences();
    if (!missionId) {
      void listMissionSummaries("all")
        .then((result) => setMissions(result.items))
        .catch(() => setMissions([]));
    }
  }, [loadPreferences, missionId]);

  const effectiveFilters = useMemo<WorkViewFilters>(() => ({
    ...(preference?.filters || {}),
    ...(missionId ? { mission_id: missionId } : {}),
  }), [missionId, preference?.filters]);

  const loadWork = useCallback(async (cursor?: string | null) => {
    if (!preference || accessLost) return;
    const sequence = ++loadSequence.current;
    cursor ? setLoadingMore(true) : setLoading(true);
    setError(null);
    try {
      const result = await listWorkspaceWork(effectiveFilters, cursor);
      if (sequence !== loadSequence.current) return;
      setItems((current) => cursor
        ? [...new Map([...current, ...result.items].map((item) => [itemKey(item), item])).values()]
        : result.items);
      setNextCursor(result.next_cursor);
    } catch (requestError) {
      if (sequence !== loadSequence.current) return;
      if (isAccessLoss(requestError)) {
        clearForAccessLoss();
      } else {
        setError(cursor ? "More Work could not be loaded. Your current list is still here." : "Work could not be loaded. Try again.");
      }
    } finally {
      if (sequence === loadSequence.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [accessLost, clearForAccessLoss, effectiveFilters, preference]);

  useEffect(() => { void loadWork(); }, [loadWork]);

  const savePreference = async (view: WorkView, filters: WorkViewFilters) => {
    if (!preference) return;
    const prior = preference;
    const optimistic = { ...preference, view, filters };
    setPreference(optimistic);
    setNotice(null);
    try {
      const result = await putWorkViewPreference({
        expected_revision: prior.revision,
        scope,
        view,
        filters,
      });
      setPreference(result.work_view_preference);
    } catch (requestError) {
      if (isAccessLoss(requestError)) {
        clearForAccessLoss();
        return;
      }
      if (requestError instanceof ApiError && requestError.status === 409) {
        const latest = await loadPreferences();
        if (latest) setNotice("This saved view changed elsewhere. We loaded the latest version.");
        return;
      }
      setPreference(prior);
      setNotice("This view could not be saved. Your previous saved view is still active.");
    }
  };

  const updateFilters = (next: WorkViewFilters) => void savePreference(preference?.view || "list", next);
  const missionNames = useMemo(() => new Map(missions.map((mission) => [mission.id, mission.title])), [missions]);
  const counts = useMemo(() => new Map(buckets.map((bucket) => [bucket.id, items.filter((item) => item.state === bucket.id).length])), [items]);

  const routeFocus = useMemo(() => {
    const query = new URLSearchParams(window.location.search);
    if (focusItemId || query.get("item")) return { kind: "item", id: focusItemId || query.get("item")! } as const;
    for (const kind of ["run", "approval", "output"] as const) {
      const id = query.get(kind);
      if (id) return { kind, id };
    }
    return null;
  }, [focusItemId, focusAction, items]);

  const routedAction = focusAction || new URLSearchParams(window.location.search).get("action");

  useEffect(() => {
    if (!routeFocus) return;
    const item = routeFocus.kind === "item"
      ? items.find((candidate) => candidate.source_id === routeFocus.id)
      : items.find((candidate) => candidate.source_type === routeFocus.kind && candidate.source_id === routeFocus.id)
        || items.find((candidate) => Object.values(candidate.action_targets || {}).some((target) => target.kind === routeFocus.kind && target.id === routeFocus.id));
    if (item && selected?.item.source_id === item.source_id) return;
    if (item) setSelected({ item, opener: null });
  }, [items, routeFocus, selected?.item.source_id]);

  const closeDetail = () => {
    setSelected(null);
    const query = new URLSearchParams(window.location.search);
    if (["item", "run", "approval", "output", "action"].some((key) => query.has(key))) {
      ["item", "run", "approval", "output", "action"].forEach((key) => query.delete(key));
      const suffix = query.toString();
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
  };

  const openLocalDetails = (item: WorkItem, opener: HTMLElement) => {
    setSelected({ item, opener });
    const query = new URLSearchParams(window.location.search);
    query.set("item", item.source_id);
    if (!missionId) query.set("mission_id", item.mission_id);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${query.toString()}`);
  };

  const openActionSurface = (item: WorkItem, actionId: string) => {
    const encodedMission = encodeURIComponent(item.mission_id);
    const target = matchingTarget(item, actionId);
    if (actionId === "verify_output" && target?.file_id) {
      window.history.pushState({}, "", `/missions/${encodedMission}/files?output=${encodeURIComponent(target.file_id)}&action=verify_output`);
      window.dispatchEvent(new PopStateEvent("popstate"));
      return;
    }
    const targetQuery = target && ["run", "approval", "output"].includes(target.kind)
      ? `${target.kind}=${encodeURIComponent(target.id)}`
      : `item=${encodeURIComponent(item.source_id)}`;
    const next = actionId === "review_plan"
      ? `/missions/${encodedMission}/conversation?focus=plan-approval`
      : `/missions/${encodedMission}/work?${targetQuery}&action=${encodeURIComponent(actionId)}`;
    window.history.pushState({}, "", next);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  const performWorkAction = async (item: WorkItem, actionId: string, input: WorkMutationInput = {}): Promise<WorkMutationOutcome> => {
    const target = matchingTarget(item, actionId);
    if (!target) return { kind: "error", message: "This action is no longer available. Refresh Work to continue safely." };
    const success: Record<string, string> = {
      claim_work: "Ownership claimed.",
      update_work: "Work updated.",
      review_work: "Review saved.",
      decide_checkpoint: input.decision === "reject" ? "Decision rejected." : "Decision approved.",
      verify_output: "Output verified.",
      retry_work: "Work restarted.",
    };
    try {
      if (actionId === "claim_work") await claimCmul8Task(item.mission_id, target.id, target.revision);
      else if (actionId === "update_work" && input.state) await transitionCmul8Task(item.mission_id, target.id, input.state, target.revision);
      else if (actionId === "review_work" && input.decision && ["approve", "request_changes", "reject"].includes(input.decision)) await reviewCmul8Task(item.mission_id, target.id, input.decision, input.note || "", target.revision);
      else if (actionId === "decide_checkpoint" && (input.decision === "approve" || input.decision === "reject") && Number.isInteger(target.run_revision)) await decideMissionCheckpoint(item.mission_id, target.id, input.decision, target.revision, target.run_revision!);
      else if (actionId === "retry_work") await retryMissionRun(item.mission_id, target.id, target.revision);
      else return { kind: "error", message: "This action is no longer available. Refresh Work to continue safely." };
      await loadWork();
      return { kind: "success", message: success[actionId] || "Change saved." };
    } catch (requestError) {
      if (isAccessLoss(requestError)) {
        clearForAccessLoss();
        return { kind: "error", message: "You no longer have access to this Work." };
      }
      if (requestError instanceof ApiError && requestError.status === 409) {
        await loadWork();
        return { kind: "error", message: "This Work changed. We refreshed it so you can review the latest details." };
      }
      return { kind: "error", message: "This change could not be saved. Your Work is unchanged." };
    }
  };

  if (!preference) return <div className="work-state" role="status">Loading Missions Work…</div>;
  if (accessLost) return <div className="work-state is-error" role="alert"><strong>You no longer have access to this Work.</strong><span>Return to Missions to continue with work you can access.</span></div>;

  const renderRow = (item: WorkItem) => {
    const action = publicPrimaryAction(item);
    return <article className="work-row" key={itemKey(item)}>
      <button className="work-row-main" type="button" onClick={(event) => openLocalDetails(item, event.currentTarget)} aria-label={`Open ${item.title}`}>
        <span className={`work-state-marker is-${item.state}`} aria-hidden="true" />
        <span className="work-row-copy"><strong>{item.title}</strong><small>{item.summary}</small></span>
        <span className="work-row-context">
          {!missionId ? <small>{missionNames.get(item.mission_id) || "Mission"}</small> : null}
          <small>{item.assignee?.display_name || "Unassigned"}</small>
          <small>{relativeTime(item.updated_at)}</small>
        </span>
      </button>
      {action ? <button className="work-primary-action" type="button" onClick={() => openActionSurface(item, action.id)}>{action.label}</button> : null}
    </article>;
  };

  return <section className="work-ledger" aria-label={missionId ? "Mission Work" : "Workspace Work"}>
    <header className="work-ledger-header">
      <div><p className="workplace-eyebrow">Work</p><h2>{missionId ? "Mission Work" : "Work across Missions"}</h2><p>Assignments, reviews, and decisions in one operational ledger.</p></div>
      <div className="work-view-switch" aria-label="Work view">
        <button type="button" aria-pressed={preference.view === "list"} onClick={() => void savePreference("list", preference.filters)}>List</button>
        <button type="button" aria-pressed={preference.view === "board"} onClick={() => void savePreference("board", preference.filters)}>Board</button>
      </div>
    </header>

    <div className="work-summary" aria-label="Work summary">
      {buckets.map((bucket) => <button key={bucket.id} type="button" onClick={() => updateFilters({ ...preference.filters, bucket: bucket.id })}>
        <strong>{counts.get(bucket.id) || 0}</strong><span>{bucket.label}</span>
      </button>)}
    </div>

    <div className="work-filters">
      <button type="button" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>Filters{Object.keys(preference.filters).length ? ` (${Object.keys(preference.filters).length})` : ""}</button>
      {Object.entries(preference.filters).map(([key, value]) => <button key={key} type="button" className="work-filter-chip" onClick={() => {
        const next = { ...preference.filters };
        delete next[key as keyof WorkViewFilters];
        updateFilters(next);
      }}>{key === "bucket" ? stateLabel(value as WorkBucket) : key === "mission_id" ? missionNames.get(String(value)) || "Selected Mission" : "Selected owner"} ×</button>)}
      {filtersOpen ? <div className="work-filter-panel">
        <label>State<select aria-label="State" value={preference.filters.bucket || ""} onChange={(event) => updateFilters({ ...preference.filters, bucket: (event.target.value || undefined) as WorkBucket | undefined })}>
          <option value="">All states</option>{buckets.map((bucket) => <option value={bucket.id} key={bucket.id}>{bucket.label}</option>)}
        </select></label>
        {!missionId ? <label>Mission<select aria-label="Mission" value={preference.filters.mission_id || ""} onChange={(event) => updateFilters({ ...preference.filters, mission_id: event.target.value || undefined })}>
          <option value="">All Missions</option>{missions.map((mission) => <option value={mission.id} key={mission.id}>{mission.title}</option>)}
        </select></label> : null}
        <label>Owner<select aria-label="Owner" value={preference.filters.assignee_id || ""} onChange={(event) => updateFilters({ ...preference.filters, assignee_id: event.target.value || undefined })}>
          <option value="">All owners</option>{[...new Map(items.filter((item) => item.assignee).map((item) => [item.assignee!.id, item.assignee!])).values()].map((assignee) => <option value={assignee.id} key={assignee.id}>{assignee.display_name}</option>)}
        </select></label>
      </div> : null}
    </div>

    {notice ? <p className="work-notice" role="status">{notice}</p> : null}
    {loading ? <div className="work-state" role="status">Loading Missions Work…</div> : error && !items.length ? <div className="work-state is-error" role="alert"><strong>{error}</strong><button type="button" onClick={() => void loadWork()}>Retry</button></div> : preference.view === "list" ? <div className="work-list">
      {buckets.map((bucket) => {
        const rows = items.filter((item) => item.state === bucket.id);
        return rows.length ? <section key={bucket.id} className="work-group" aria-labelledby={`work-${scope}-${bucket.id}`}><h3 id={`work-${scope}-${bucket.id}`}>{bucket.label} <span>{rows.length}</span></h3>{rows.map(renderRow)}</section> : null;
      })}
      {!items.length ? <div className="work-state">No Work matches this view.</div> : null}
    </div> : <div className="work-board">
      {buckets.map((bucket) => <section className="work-board-column" key={bucket.id}><h3>{bucket.label} <span>{counts.get(bucket.id) || 0}</span></h3>{items.filter((item) => item.state === bucket.id).map(renderRow)}</section>)}
    </div>}
    {error && items.length ? <div className="work-notice is-error" role="alert">{error}<button type="button" onClick={() => void loadWork(nextCursor)}>Retry</button></div> : null}
    {nextCursor ? <button className="work-load-more" type="button" disabled={loadingMore} onClick={() => void loadWork(nextCursor)}>{loadingMore ? "Loading…" : "Load more Work"}</button> : null}

    {selected ? <>
      <button className="work-detail-scrim" type="button" aria-label="Close Work details backdrop" onClick={closeDetail} />
      <WorkDetail
        selected={selected}
        missionName={missionId ? "This Mission" : missionNames.get(selected.item.mission_id) || "Mission"}
        focusAction={routedAction}
        onSubmit={(action, input) => performWorkAction(selected.item, action, input)}
        onReviewPlan={() => openActionSurface(selected.item, "review_plan")}
        onReviewOutput={() => openActionSurface(selected.item, "verify_output")}
        onClose={closeDetail}
      />
    </> : null}
  </section>;
}
