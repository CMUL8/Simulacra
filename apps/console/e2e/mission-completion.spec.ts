import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const artifactRoot = fileURLToPath(new URL("../../../artifacts/ui-acceptance/", import.meta.url));
const missionId = "mission_completion";
const outputFileId = `file_${"2".repeat(40)}`;
const outputId = "deliverable_completion";
const privateTerms = ["codex", "runtime", "provider", "model", "worker", "Traceback", "/app/"];

type Phase = "ready" | "completed" | "verified";

function message(
  id: string,
  kind: string,
  author: { id: string; kind: "human" | "agent"; display_name: string },
  body: string,
  links: { work_item_id: string | null; run_id: string | null; output_id: string | null },
) {
  return {
    id,
    mission_id: missionId,
    kind,
    author: { ...author, avatar_url: null },
    body,
    created_at: id === "message_assignment" ? "2026-08-29T09:00:00Z" : "2026-08-29T09:04:00Z",
    edited_at: null,
    thread: { reply_count: 0, latest_replies: [] },
    reactions: [],
    saved: false,
    links,
  };
}

async function installMissionFixture(page: Page) {
  let phase: Phase = "ready";
  let assignmentCount = 0;
  let verificationCount = 0;
  let attentionRead = false;
  const assignment = message(
    "message_assignment",
    "assignment_created",
    { id: "human_ada", kind: "human", display_name: "Ada" },
    "@Fin reconcile invoice 42 and prepare a reviewable report",
    { work_item_id: "task_completion", run_id: "run_completion", output_id: null },
  );
  const completion = message(
    "message_completion",
    "agent_completed",
    { id: "agent_fin", kind: "agent", display_name: "Fin" },
    "Work completed. An output is ready for human verification.",
    { work_item_id: "task_completion", run_id: "run_completion", output_id: outputId },
  );
  const started = {
    ...message(
      "message_started",
      "agent_started",
      { id: "agent_fin", kind: "agent", display_name: "Fin" },
      "Working on the assignment. Progress and questions will return here.",
      { work_item_id: "task_completion", run_id: "run_completion", output_id: null },
    ),
    created_at: "2026-08-29T09:01:00Z",
  };

  await page.addInitScript(() => {
    localStorage.setItem("simulacra_token", "mission-completion-session");
    localStorage.setItem("simulacra_tenant_id", "tenant_completion");
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    let payload: unknown;

    if (path === "/auth/me") {
      payload = {
        user: { id: "human_ada", email: "ada@example.test", name: "Ada" },
        tenant_id: "tenant_completion",
        role: "owner",
        tenants: [{ id: "tenant_completion", name: "Northstar Finance" }],
        workplace_flags: {
          workplace_shell_v1: true,
          workplace_attention_v1: true,
          workplace_conversation_v1: true,
          workplace_files_v1: true,
          workplace_preview_origin_v1: true,
          workplace_sse_v1: false,
          workplace_bootstrap_v1: true,
        },
      };
    } else if (path === "/missions") {
      payload = {
        items: [{
          id: missionId,
          title: "Reconcile invoice 42",
          outcome_summary: "Resolve the exception and return a report with evidence.",
          public_state: phase === "verified" ? "completed" : "active",
          updated_at: "2026-08-29T09:04:00Z",
          human_count: 1,
          agent_count: 1,
          active_work_count: phase === "verified" ? 0 : 1,
          needs_human_count: phase === "completed" ? 1 : 0,
          verified_output_count: phase === "verified" ? 1 : 0,
          current_human_permissions: ["read", "review"],
        }],
        next_cursor: null,
      };
    } else if (path === "/workspace/preferences") {
      payload = {
        work_view_preferences: [{
          scope: `mission:${missionId}`,
          view: "list",
          filters: {},
          revision: 1,
          updated_at: "2026-08-29T08:00:00Z",
        }],
        notification_preference: {
          event_selection: "actionable",
          channels: ["in_app"],
          digest: "instant",
          muted_mission_ids: [],
          revision: 1,
          updated_at: "2026-08-29T08:00:00Z",
        },
      };
    } else if (path === `/projects/${missionId}/mission`) {
      payload = {
        mission: {
          title: "Reconcile invoice 42",
          objective: "Resolve the exception and return a report with evidence.",
        },
        agents: [{ id: "agent_fin", name: "Fin", role: "Reconciliation analyst" }],
        runs: phase === "ready" ? [] : [{ id: "run_completion", status: "succeeded", assigned_agent_ids: ["agent_fin"] }],
        triggers: [],
        deliverables: phase === "ready" ? [] : [{ id: outputId, name: "invoice-42-report.md", state: phase === "verified" ? "verified" : "awaiting_verification" }],
        events: [],
        approvals: [],
        readiness: { graph: { status: "approved", revision: 1 }, crew_count: 1 },
      };
    } else if (path === `/projects/${missionId}/cmul8/room`) {
      payload = {
        room: {
          id: "room_completion",
          revision: 1,
          members: [{ actor_id: "human_ada", actor_type: "human", display_name: "Ada", role: "owner" }],
        },
        project: { id: missionId, name: "Reconcile invoice 42", objective: "Resolve the exception and return a report with evidence." },
        tasks: [], comments: [], reviews: [], events: [],
        away: { total: 0, unread: 0, counts: {}, highlights: [] },
        permissions: { review_graph: true, invite: true },
        presence: [],
      };
    } else if (path === `/projects/${missionId}/conversation` && method === "GET") {
      payload = { items: phase === "ready" ? [] : [assignment, started, completion], next_before: null };
    } else if (path === `/projects/${missionId}/conversation/messages` && method === "POST") {
      const body = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      expect(body).toMatchObject({
        mode: "assignment",
        assignee_agent_ids: ["agent_fin"],
        reviewer_human_ids: [],
      });
      expect(String(body.client_request_id || "")).not.toBe("");
      assignmentCount += 1;
      phase = "completed";
      payload = {
        message: assignment,
        work_item: {
          id: "task_completion",
          title: "Reconcile invoice 42",
          state: "queued",
          assignee_agent_ids: ["agent_fin"],
          reviewer_human_ids: [],
          allowed_actions: ["open"],
        },
      };
    } else if (path === "/workspace/work") {
      payload = {
        items: phase === "ready" ? [] : [{
          source_type: "task",
          source_id: "task_completion",
          mission_id: missionId,
          revision: phase === "verified" ? 2 : 1,
          title: "Review invoice 42 report",
          summary: phase === "verified" ? "The exact report was verified by Ada." : "Fin returned a report with evidence for human verification.",
          state: phase === "verified" ? "done" : "ready_for_review",
          assignee: { id: "agent_fin", display_name: "Fin", kind: "agent", avatar_url: null },
          created_at: "2026-08-29T09:04:00Z",
          updated_at: "2026-08-29T09:04:00Z",
          allowed_actions: phase === "verified" ? ["open"] : ["open", "verify_output"],
          action_targets: phase === "verified" ? {} : {
            verify_output: { kind: "output", id: outputId, file_id: outputFileId, revision: 1 },
          },
        }],
        next_cursor: null,
      };
    } else if (path === `/projects/${missionId}/files/${outputFileId}/content` && method === "GET") {
      expect(url.searchParams.get("disposition")).toBe("inline");
      await route.fulfill({
        status: 200,
        contentType: "text/markdown",
        body: "# Invoice 42 exception report\n\nThe purchase order total does not match the invoice.\n\nEvidence: invoice-42.csv, row 42.\n",
      });
      return;
    } else if (path === `/projects/${missionId}/files` && method === "GET") {
      const source = {
        id: "file_source_completion", mission_id: missionId, kind: "source", name: "invoice-42.csv",
        media_type: "text/csv", size: 240, sha256: "a".repeat(64), state: "ready", version: 1,
        parent_output_id: null, producer_id: null, producer: null, verifier: null, source_ids: [],
        introduced_by_message_id: "message_assignment", created_at: "2026-08-29T09:00:00Z", updated_at: "2026-08-29T09:00:00Z",
        previewable: true, downloadable: true, allowed_actions: [], action_targets: {},
      };
      const output = {
        id: outputFileId, mission_id: missionId, kind: "output", name: "invoice-42-report.md",
        media_type: "text/markdown", size: 640, sha256: "b".repeat(64), state: phase === "verified" ? "verified" : "awaiting_verification", version: 1,
        parent_output_id: null, producer_id: "agent_fin", producer: { id: "agent_fin", display_name: "Fin" },
        verifier: phase === "verified" ? { id: "human_ada", display_name: "Ada" } : null,
        source_ids: [source.id], introduced_by_message_id: "message_completion", created_at: "2026-08-29T09:04:00Z", updated_at: "2026-08-29T09:04:00Z",
        previewable: true, downloadable: true,
        allowed_actions: phase === "verified" ? [] : ["verify_output"],
        action_targets: phase === "verified" ? {} : { verify_output: { kind: "output", id: outputId, revision: 1 } },
      };
      const evidence = {
        id: "file_evidence_completion", mission_id: missionId, kind: "evidence", name: "Invoice reconciliation checks",
        media_type: "application/json", size: 180, sha256: "c".repeat(64), state: "recorded", version: 1,
        parent_output_id: outputFileId, producer_id: "agent_fin", producer: { id: "agent_fin", display_name: "Fin" },
        verifier: null, source_ids: [source.id], introduced_by_message_id: "message_completion", created_at: "2026-08-29T09:04:00Z", updated_at: "2026-08-29T09:04:00Z",
        previewable: true, downloadable: true, allowed_actions: [], action_targets: {},
      };
      payload = { items: phase === "ready" ? [source] : [source, output, evidence], files: [] };
    } else if (path === `/projects/${missionId}/mission/deliverables/${outputId}/verify` && method === "POST") {
      expect(JSON.parse(request.postData() || "{}")).toEqual({ decision: "verify", expected_version: 1 });
      verificationCount += 1;
      phase = "verified";
      payload = { id: outputId, state: "verified", version: 1 };
    } else if (path === "/workspace/attention/read" && method === "POST") {
      expect(JSON.parse(request.postData() || "{}")).toEqual({ event_id: "attention_completion", expected_revision: 1 });
      attentionRead = true;
      payload = {
        item: {
          id: "attention_completion", mission_id: missionId, type: "output_verification",
          title: "Output ready to verify", summary: "invoice-42-report.md", source_event_id: "event_completion",
          subject_id: outputId, priority: 15, actionable: true, read: true, revision: 2,
          created_at: "2026-08-29T09:04:00Z", updated_at: "2026-08-29T09:04:00Z",
          deep_link: `/missions/${missionId}/files?output=${outputFileId}&action=verify_output`,
          allowed_actions: ["open", "verify_output"],
        },
      };
    } else if (path === "/workspace/attention") {
      const items = phase === "completed" ? [{
        id: "attention_completion", mission_id: missionId, type: "output_verification",
        title: "Output ready to verify", summary: "invoice-42-report.md", source_event_id: "event_completion",
        subject_id: outputId, priority: 15, actionable: true, read: attentionRead, revision: attentionRead ? 2 : 1,
        created_at: "2026-08-29T09:04:00Z", updated_at: "2026-08-29T09:04:00Z",
        deep_link: `/missions/${missionId}/files?output=${outputFileId}&action=verify_output`,
        allowed_actions: ["open", "verify_output"],
      }] : [];
      payload = { items, next_cursor: null, unread_count: items.length, actionable_count: items.length };
    } else {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: { message: `Fixture route unavailable: ${method} ${path}` } }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });

  return {
    assignmentCount: () => assignmentCount,
    verificationCount: () => verificationCount,
  };
}

async function expectNoPrivateTerms(page: Page) {
  const publicText = (await page.locator("body").innerText()).toLowerCase();
  for (const privateTerm of privateTerms) {
    expect(publicText).not.toContain(privateTerm.toLowerCase());
  }
}

test("human_assigns_real_work_returns_and_verifies_the_exact_agent_output", async ({ page }) => {
  test.setTimeout(60_000);
  await mkdir(artifactRoot, { recursive: true });
  const fixture = await installMissionFixture(page);

  await page.goto(`/missions/${missionId}/conversation`);
  await expect(page.getByRole("heading", { name: "Reconcile invoice 42" })).toBeVisible();
  const composer = page.getByRole("textbox", { name: "Message the Mission" });
  await composer.fill("@");
  await page.getByRole("option", { name: /Fin/ }).click();
  await composer.fill("@Fin reconcile invoice 42 and prepare a reviewable report");
  await page.getByRole("button", { name: "Assign work", exact: true }).click();
  await expect(page.getByText("@Fin reconcile invoice 42 and prepare a reviewable report")).toBeVisible();
  expect(fixture.assignmentCount()).toBe(1);

  // Leaving and returning must rebuild the room from durable product state.
  await page.goto("/missions?state=active");
  await page.goto(`/missions/${missionId}/conversation`);
  const progress = page.locator(".conversation-message.is-progress");
  await expect(progress).toContainText("Work started");
  await expect(progress).toContainText("Working on the assignment");
  await expect(page.getByText("Work completed. An output is ready for human verification.")).toBeVisible();
  await expectNoPrivateTerms(page);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-conversation.png`, fullPage: true });
  await page.getByRole("button", { name: "Review output" }).click();
  await expect(page).toHaveURL(new RegExp(`/missions/${missionId}/work\\?item=task_completion&action=verify_output$`));
  await expect(
    page.getByLabel("Work details", { exact: true }).getByText(
      "Fin returned a report with evidence for human verification.",
    ),
  ).toBeVisible();

  await page.getByTitle("Needs you").click();
  await expect(page.getByRole("button", { name: /Output ready to verify/ })).toBeVisible();
  await expect(page.getByText("invoice-42-report.md", { exact: true })).toBeVisible();
  await expectNoPrivateTerms(page);
  await page.mouse.move(700, 500);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-needs-you.png`, fullPage: true });
  await page.getByRole("button", { name: /Output ready to verify/ }).click();
  await expect(page).toHaveURL(new RegExp(`/missions/${missionId}/files\\?output=${outputFileId}&action=verify_output$`));

  const missionViews = page.getByRole("navigation", { name: "Mission views" });
  await missionViews.getByRole("button", { name: "Work" }).click();
  await expect(page.getByRole("heading", { name: "Mission Work" })).toBeVisible();
  await expect(page.getByText("Fin returned a report with evidence for human verification.")).toBeVisible();
  await expectNoPrivateTerms(page);
  await page.getByRole("button", { name: "Review evidence" }).click();
  await expect(page).toHaveURL(new RegExp(`/missions/${missionId}/files\\?output=${outputFileId}&action=verify_output$`));

  await expect(page.getByRole("heading", { name: "Sources, outputs, and evidence" })).toBeVisible();
  const details = page.getByRole("dialog", { name: "File details" });
  await expect(details.getByText("Invoice reconciliation checks")).toBeVisible();
  await expect(details.getByRole("button", { name: "Open source invoice-42.csv" })).toBeVisible();
  await expect(details.getByText("1", { exact: true }).first()).toBeVisible();
  await details.getByRole("button", { name: "Preview" }).click();
  const preview = page.getByRole("dialog", { name: "invoice-42-report.md preview" });
  await expect(preview).toBeVisible();
  const previewFrame = page.frameLocator('iframe[title="invoice-42-report.md preview"]');
  await expect(previewFrame.locator("body")).toContainText("Invoice 42 exception report");
  await expect(previewFrame.locator("body")).toContainText("invoice-42.csv, row 42");
  await page.mouse.move(700, 500);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-output-preview.png`, fullPage: true });
  await preview.getByRole("button", { name: "Close file preview" }).click();
  await page.getByRole("button", { name: "Open invoice-42-report.md details" }).click();
  await expectNoPrivateTerms(page);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-verification.png`, fullPage: true });
  await details.getByRole("button", { name: "Verify this output" }).click();
  await expect(details.getByText("Verified by Ada.")).toBeVisible();
  expect(fixture.verificationCount()).toBe(1);

  await page.getByTitle("Needs you").click();
  await expect(page.getByText("You are all caught up.")).toBeVisible();
  await page.mouse.move(700, 500);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-verified.png`, fullPage: true });

  await expectNoPrivateTerms(page);
});

test("mission_progress_and_crew_stay_compact_on_mobile", async ({ page }) => {
  test.setTimeout(45_000);
  await mkdir(artifactRoot, { recursive: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await installMissionFixture(page);

  await page.goto(`/missions/${missionId}/conversation`);
  const composer = page.getByRole("textbox", { name: "Message the Mission" });
  await composer.fill("@");
  await page.getByRole("option", { name: /Fin/ }).click();
  await composer.fill("@Fin reconcile invoice 42 and prepare a reviewable report");
  await page.getByRole("button", { name: "Assign work", exact: true }).click();
  await page.goto(`/missions/${missionId}/conversation`);

  await expect(page.locator(".conversation-message.is-progress")).toContainText("Work started");
  await expect(page.getByRole("button", { name: "Review output" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Show" }).click();
  await expect(page.getByRole("complementary", { name: "Mission crew" })).toHaveClass(/is-open/);
  await expect(page.getByRole("button", { name: "Mention Fin" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expectNoPrivateTerms(page);
  await page.screenshot({ path: `${artifactRoot}/mission-completion-mobile.png`, fullPage: true });
});
