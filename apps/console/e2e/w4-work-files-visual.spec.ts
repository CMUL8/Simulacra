import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const artifactRoot = fileURLToPath(new URL("../../../artifacts/ui-acceptance/", import.meta.url));

const mission = {
  id: "mission_close",
  title: "Close August with verified evidence",
  outcome_summary: "Reconcile every invoice and prepare the close pack for human approval.",
  public_state: "active",
  updated_at: "2026-08-28T08:30:00Z",
  human_count: 2,
  agent_count: 2,
  active_work_count: 2,
  needs_human_count: 1,
  verified_output_count: 1,
  current_human_permissions: ["read", "review"],
};

const workItems = [
  {
    source_type: "task", source_id: "task_review", mission_id: mission.id, revision: 4,
    title: "Review invoice exceptions", summary: "Three exceptions are ready for a human decision.",
    state: "needs_you", assignee: { id: "human_ada", display_name: "Ada", kind: "human" },
    created_at: "2026-08-28T07:00:00Z", updated_at: "2026-08-28T08:30:00Z",
    allowed_actions: ["open", "verify_output"],
    action_targets: { verify_output: { kind: "output", id: "output_close", file_id: "file_2222222222222222222222222222222222222222", revision: 3 } },
  },
  {
    source_type: "task", source_id: "task_reconcile", mission_id: mission.id, revision: 3,
    title: "Reconcile invoice ledger", summary: "Matching purchase orders and investigating variances.",
    state: "in_progress", assignee: { id: "agent_fin", display_name: "Fin", kind: "agent" },
    created_at: "2026-08-28T06:30:00Z", updated_at: "2026-08-28T08:10:00Z",
    allowed_actions: ["open"], action_targets: {},
  },
  {
    source_type: "task", source_id: "task_pack", mission_id: mission.id, revision: 6,
    title: "Prepare monthly close pack", summary: "Report and supporting evidence are verified.",
    state: "done", assignee: { id: "agent_fin", display_name: "Fin", kind: "agent" },
    created_at: "2026-08-28T05:30:00Z", updated_at: "2026-08-28T08:00:00Z",
    allowed_actions: ["open"], action_targets: {},
  },
];

const files = [
  {
    id: "file_1111111111111111111111111111111111111111", mission_id: mission.id, kind: "source",
    name: "invoice-ledger.csv", media_type: "text/csv", size: 18420, sha256: "a".repeat(64), state: "ready", version: 1,
    producer_id: null, producer: null, verifier: null, run_id: null, parent_output_id: null, source_ids: [],
    introduced_by_message_id: "message_source", created_at: "2026-08-28T06:00:00Z", updated_at: "2026-08-28T06:00:00Z",
    previewable: true, downloadable: true, allowed_actions: [], action_targets: {},
  },
  {
    id: "file_2222222222222222222222222222222222222222", mission_id: mission.id, kind: "output",
    name: "August close pack.pdf", media_type: "application/pdf", size: 486000, sha256: "b".repeat(64), state: "verified", version: 3,
    producer_id: "agent_fin", producer: { id: "agent_fin", display_name: "Fin" }, verifier: { id: "human_ada", display_name: "Ada" },
    run_id: "run_close", parent_output_id: null, source_ids: ["file_1111111111111111111111111111111111111111"],
    introduced_by_message_id: "message_output", created_at: "2026-08-28T07:30:00Z", updated_at: "2026-08-28T08:00:00Z",
    previewable: false, downloadable: true, allowed_actions: [], action_targets: {},
  },
  {
    id: "file_3333333333333333333333333333333333333333", mission_id: mission.id, kind: "evidence",
    name: "Reconciliation checks.json", media_type: "application/json", size: 2400, sha256: "c".repeat(64), state: "recorded", version: 3,
    producer_id: "agent_fin", producer: { id: "agent_fin", display_name: "Fin" }, verifier: { id: "human_ada", display_name: "Ada" },
    run_id: "run_close", parent_output_id: "file_2222222222222222222222222222222222222222", source_ids: ["file_1111111111111111111111111111111111111111"],
    introduced_by_message_id: "message_output", created_at: "2026-08-28T07:30:00Z", updated_at: "2026-08-28T08:00:00Z",
    previewable: true, downloadable: true, allowed_actions: [], action_targets: {},
  },
];

async function installFixture(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("simulacra_token", "visual-session");
    localStorage.setItem("simulacra_tenant_id", "tenant_visual");
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    let payload: unknown = {};
    if (path === "/auth/me") payload = {
      user: { id: "human_ada", email: "ada@example.test", name: "Ada" }, tenant_id: "tenant_visual", role: "owner",
      tenants: [{ id: "tenant_visual", name: "Northstar Finance" }],
      workplace_flags: { workplace_shell_v1: true, workplace_attention_v1: true, workplace_conversation_v1: true, workplace_files_v1: true, workplace_preview_origin_v1: false, workplace_sse_v1: false, workplace_bootstrap_v1: true },
    };
    else if (path === "/missions") payload = { items: [mission], next_cursor: null };
    else if (path === "/workspace/preferences") payload = {
      work_view_preferences: [{ scope: "workspace", view: "list", filters: {}, revision: 1, updated_at: "2026-08-28T08:00:00Z" }],
      notification_preference: { event_selection: "actionable", channels: ["in_app"], digest: "instant", muted_mission_ids: [], revision: 1, updated_at: "2026-08-28T08:00:00Z" },
    };
    else if (path === "/workspace/work") payload = { items: workItems, next_cursor: null };
    else if (path === `/projects/${mission.id}/mission`) payload = {
      mission: { title: mission.title, objective: mission.outcome_summary },
      agents: [{ id: "agent_fin", name: "Fin", role: "Reconciliation analyst" }, { id: "agent_writer", name: "Mira", role: "Report editor" }],
      runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 2 }, crew_count: 2 },
    };
    else if (path === `/projects/${mission.id}/cmul8/room`) payload = {
      room: { id: "room_close", revision: 3, members: [{ actor_id: "human_ada", display_name: "Ada", role: "owner" }, { actor_id: "human_maya", display_name: "Maya", role: "reviewer" }] },
      project: { id: mission.id, name: mission.title, objective: mission.outcome_summary }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    };
    else if (path === `/projects/${mission.id}/conversation`) payload = { items: [], next_before: null };
    else if (path === `/projects/${mission.id}/files`) payload = { items: files, files: [] };
    else if (path === "/workspace/attention") payload = { items: [], next_cursor: null, unread_count: 0, actionable_count: 0 };
    else {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: { message: "Fixture route unavailable" } }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
}

test("w4_work_files_provenance_responsive_screenshot_matrix", async ({ page }) => {
  test.setTimeout(60_000);
  await mkdir(artifactRoot, { recursive: true });
  await installFixture(page);
  const viewports = [
    ["desktop", 1440, 1000],
    ["compact", 1180, 820],
    ["tablet", 834, 1112],
    ["mobile", 390, 844],
  ] as const;

  for (const [name, width, height] of viewports) {
    await page.setViewportSize({ width, height });
    await page.goto("/work");
    await expect(page.getByRole("heading", { name: "Work across Missions" })).toBeVisible();
    await page.screenshot({ path: `${artifactRoot}/w4-work-${name}.png`, fullPage: true });

    await page.goto(`/missions/${mission.id}/files?output=file_2222222222222222222222222222222222222222`);
    await expect(page.getByRole("heading", { name: "Sources, outputs, and evidence" })).toBeVisible();
    const outputTab = page.getByRole("tab", { name: /Outputs/ });
    await expect(outputTab).toHaveAttribute("aria-selected", "true");
    const details = page.getByRole("dialog", { name: "File details" });
    if (!(await details.isVisible())) await page.getByRole("button", { name: "Open August close pack.pdf details" }).click();
    await expect(page.getByText("Verified by Ada.")).toBeVisible();
    await expect(page.getByText("invoice-ledger.csv", { exact: true })).toBeVisible();
    await page.screenshot({ path: `${artifactRoot}/w4-files-${name}.png`, fullPage: true });

    await page.getByRole("button", { name: "Close file details", exact: true }).click();
    const fileMain = page.getByRole("button", { name: "Open August close pack.pdf details" });
    const download = page.getByRole("button", { name: "Download" });
    const [fileMainBox, downloadBox] = await Promise.all([fileMain.boundingBox(), download.boundingBox()]);
    expect(fileMainBox).not.toBeNull();
    expect(downloadBox).not.toBeNull();
    if (fileMainBox && downloadBox && width > 768) {
      expect(fileMainBox.x + fileMainBox.width).toBeLessThanOrEqual(downloadBox.x);
    }
  }
});
