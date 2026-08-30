import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { MissionFiles } from "./MissionFiles";

const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

test.each([
  ["output", "draft", "Draft"],
  ["output", "validated", "Validated"],
  ["output", "awaiting_verification", "Awaiting verification"],
  ["output", "verified", "Verified"],
  ["output", "changes_requested", "Changes requested"],
  ["output", "published", "Published"],
  ["output", "unexpected_state", "In progress"],
  ["source", "ready", "Available"],
  ["evidence", "recorded", "Recorded"],
] as const)("mission_files_labels_%s_state_%s_as_%s", async (kind, state, label) => {
  const name = `${kind}-${state}.txt`;
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [{
    id: `file_${kind}_${state}`,
    mission_id: "mission_private",
    kind,
    name,
    media_type: "text/plain",
    size: 12,
    sha256: "a".repeat(64),
    state,
    version: 1,
    parent_output_id: null,
    producer_id: kind === "source" ? null : "agent_private",
    producer: kind === "source" ? null : { id: "agent_private", display_name: "Fin" },
    verifier: null,
    source_ids: [],
    introduced_by_message_id: null,
    created_at: null,
    updated_at: null,
    previewable: false,
    downloadable: false,
  }] }));

  render(<MissionFiles missionId="mission_private" />);
  const tabName = kind === "source" ? /Sources/ : kind === "output" ? /Outputs/ : /Evidence/;
  fireEvent.click(screen.getByRole("tab", { name: tabName }));
  const row = await screen.findByRole("button", { name: `Open ${name} details` });
  expect(within(row).getByText(label, { exact: true })).toBeInTheDocument();
  if (state !== "awaiting_verification") {
    expect(within(row).queryByText("Awaiting verification", { exact: true })).not.toBeInTheDocument();
  }
});

test("mission_files_keeps_sources_outputs_and_evidence_clear_without_private_references", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [
    { id: "file_private_source", mission_id: "mission_private", kind: "source", name: "invoices.csv", media_type: "text/csv", size: 1200, sha256: "a".repeat(64), state: "ready", version: 1, parent_output_id: null, producer_id: null, producer: null, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true },
    { id: "file_private_output", mission_id: "mission_private", kind: "output", name: "Close report.pdf", media_type: "application/pdf", size: 4200, sha256: "b".repeat(64), state: "verified", version: 3, parent_output_id: null, producer_id: "agent_private", producer: { id: "agent_private", display_name: "Fin" }, verifier: { id: "human_private", display_name: "Ada" }, source_ids: ["file_private_source"], introduced_by_message_id: "message_private", created_at: null, updated_at: "2026-08-28T09:00:00Z", previewable: true, downloadable: true },
    { id: "file_private_other_output", mission_id: "mission_private", kind: "output", name: "Board report.pdf", media_type: "application/pdf", size: 4201, sha256: "d".repeat(64), state: "verified", version: 3, parent_output_id: null, producer_id: "agent_private", producer: { id: "agent_private", display_name: "Fin" }, verifier: { id: "human_private", display_name: "Ada" }, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true },
    { id: "file_private_evidence", mission_id: "mission_private", kind: "evidence", name: "Close validation summary", media_type: "application/json", size: 800, sha256: "c".repeat(64), state: "recorded", version: 3, parent_output_id: "file_private_output", producer_id: "agent_private", producer: { id: "agent_private", display_name: "Fin" }, verifier: { id: "human_private", display_name: "Ada" }, source_ids: [], introduced_by_message_id: "message_private", created_at: null, updated_at: null, previewable: true, downloadable: true },
    { id: "file_private_other_evidence", mission_id: "mission_private", kind: "evidence", name: "Board validation summary", media_type: "application/json", size: 801, sha256: "e".repeat(64), state: "recorded", version: 3, parent_output_id: "file_private_other_output", producer_id: "agent_private", producer: { id: "agent_private", display_name: "Fin" }, verifier: { id: "human_private", display_name: "Ada" }, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true },
  ] }));
  const openMessage = vi.fn();
  render(<MissionFiles missionId="mission_private" onOpenMessage={openMessage} />);
  expect(await screen.findByRole("tab", { name: /Sources/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("invoices.csv")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /Outputs/ }));
  expect(screen.getByText("Close report.pdf")).toBeInTheDocument();
  expect(screen.getAllByText("Verified by Ada")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Open Close report.pdf details" }));
  const detail = screen.getByRole("dialog", { name: "File details" });
  expect(within(detail).getByText("Close validation summary")).toBeInTheDocument();
  expect(within(detail).queryByText("Board validation summary")).not.toBeInTheDocument();
  expect(within(detail).getByRole("heading", { name: "Evidence" }).compareDocumentPosition(within(detail).getByText("Verified by Ada.")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open original message" }));
  expect(openMessage).toHaveBeenCalledWith("message_private");
  expect(document.body.textContent).not.toContain("file_private");
  expect(document.body.textContent).not.toContain("agent_private");
});

test("work_and_file_drawers_escape_trap_restore_focus_and_mobile_back_closes_preview", async () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("/content")
    ? Promise.resolve(new Response(new Blob(["hello"], { type: "text/plain" }), { status: 200 }))
    : response({ items: [{
      id: "source_readme", mission_id: "mission_private", kind: "source", name: "Read me.txt", media_type: "text/plain", size: 5,
      sha256: "a".repeat(64), state: "ready", version: 1, parent_output_id: null, producer_id: null, producer: null, verifier: null,
      source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true,
    }] }));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  window.history.replaceState({}, "", "/missions/mission_private/files");
  render(<MissionFiles missionId="mission_private" />);
  const detailsOpener = await screen.findByRole("button", { name: "Open Read me.txt details" });
  fireEvent.click(detailsOpener);
  const details = screen.getByRole("dialog", { name: "File details" });
  const detailsClose = within(details).getByRole("button", { name: "Close file details" });
  await waitFor(() => expect(detailsClose).toHaveFocus());
  fireEvent.keyDown(details, { key: "Tab", shiftKey: true });
  expect(within(details).getByRole("button", { name: "Download" })).toHaveFocus();
  fireEvent.keyDown(details, { key: "Escape" });
  await waitFor(() => expect(detailsOpener).toHaveFocus());

  fireEvent.click(detailsOpener);
  fireEvent.click(await screen.findByRole("button", { name: "Close file details backdrop" }));
  await waitFor(() => expect(detailsOpener).toHaveFocus());

  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(window.location.search).toContain("preview=source_readme");
  expect(await screen.findByRole("dialog", { name: "Read me.txt preview" })).toBeInTheDocument();
  window.history.replaceState({}, "", "/missions/mission_private/files");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Read me.txt preview" })).not.toBeInTheDocument());
});

test("mission_files_clears_protected_rows_when_access_is_lost", async () => {
  const denied = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ detail: { message: "forbidden/private/path" } }, 403));
  render(<MissionFiles missionId="mission_private" onAccessLost={denied} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByText("forbidden/private/path")).not.toBeInTheDocument();
  expect(denied).toHaveBeenCalled();
});

test("output_deep_link_opens_exact_returned_output_and_shows_safe_source_provenance", async () => {
  const source = { id: "source_opaque", mission_id: "mission_private", kind: "source", name: "August invoices.csv", media_type: "text/csv", size: 1200, sha256: "a".repeat(64), state: "ready", version: 1, parent_output_id: null, producer_id: null, producer: null, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true };
  const output = { id: "output_opaque", mission_id: "mission_private", kind: "output", name: "Close report.pdf", media_type: "application/pdf", size: 4200, sha256: "b".repeat(64), state: "awaiting_verification", version: 2, parent_output_id: null, producer_id: "agent_opaque", producer: { id: "agent_opaque", display_name: "Fin" }, verifier: null, source_ids: [source.id, "authorized_but_not_in_page"], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true };
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [source, output] }));
  window.history.replaceState({}, "", `/missions/mission_private/files?output=${output.id}`);
  render(<MissionFiles missionId="mission_private" />);
  const detail = await screen.findByRole("dialog", { name: "File details" });
  expect(screen.getByRole("tab", { name: /Outputs/ })).toHaveAttribute("aria-selected", "true");
  expect(within(detail).getByRole("button", { name: "Open source August invoices.csv" })).toBeInTheDocument();
  expect(within(detail).getByText("Source unavailable")).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("source_opaque");
  expect(document.body.textContent).not.toContain("authorized_but_not_in_page");
});

test("opening_preview_from_file_details_leaves_only_one_modal", async () => {
  const file = { id: "source_readme", mission_id: "mission_private", kind: "source", name: "Read me.txt", media_type: "text/plain", size: 5, sha256: "a".repeat(64), state: "ready", version: 1, parent_output_id: null, producer_id: null, producer: null, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true };
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("/content")
    ? Promise.resolve(new Response(new Blob(["hello"], { type: "text/plain" }), { status: 200 }))
    : response({ items: [file] }));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  render(<MissionFiles missionId="mission_private" />);
  fireEvent.click(await screen.findByRole("button", { name: "Open Read me.txt details" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "File details" })).getByRole("button", { name: "Preview" }));
  expect(await screen.findByRole("dialog", { name: "Read me.txt preview" })).toBeInTheDocument();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
});

test("output_verification_is_offered_only_after_exact_evidence_and_uses_the_server_action_target", async () => {
  const source = { id: "source_exact", mission_id: "mission_private", kind: "source", name: "Invoices.csv", media_type: "text/csv", size: 1200, sha256: "a".repeat(64), state: "ready", version: 1, parent_output_id: null, producer_id: null, producer: null, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true, allowed_actions: [], action_targets: {} };
  const output = { id: "file_exact", mission_id: "mission_private", kind: "output", name: "Close report.pdf", media_type: "application/pdf", size: 4200, sha256: "b".repeat(64), state: "awaiting_verification", version: 2, parent_output_id: null, producer_id: "agent_exact", producer: { id: "agent_exact", display_name: "Fin" }, verifier: null, source_ids: [source.id], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true, allowed_actions: ["verify_output"], action_targets: { verify_output: { kind: "output", id: "deliverable_exact", revision: 12 } } };
  const evidence = { id: "evidence_exact", mission_id: "mission_private", kind: "evidence", name: "Reconciliation checks", media_type: "application/json", size: 500, sha256: "c".repeat(64), state: "recorded", version: 2, parent_output_id: output.id, producer_id: "agent_exact", producer: { id: "agent_exact", display_name: "Fin" }, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true, allowed_actions: [], action_targets: {} };
  const mutationBodies: Array<Record<string, unknown>> = [];
  let fileReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/mission/deliverables/deliverable_exact/verify")) {
      mutationBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return response({});
    }
    if (path.includes("/files?")) {
      fileReads += 1;
      return response({ items: [source, output, evidence] });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  window.history.replaceState({}, "", "/missions/mission_private/files?output=file_exact&action=verify_output");
  render(<MissionFiles missionId="mission_private" />);
  const detail = await screen.findByRole("dialog", { name: "File details" });
  const sourceHeading = within(detail).getByRole("heading", { name: "Sources" });
  const evidenceHeading = within(detail).getByRole("heading", { name: "Evidence" });
  const decisionHeading = within(detail).getByRole("heading", { name: "Human decision" });
  expect(sourceHeading.compareDocumentPosition(decisionHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(evidenceHeading.compareDocumentPosition(decisionHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(detail).getByText("Reconciliation checks")).toBeInTheDocument();
  fireEvent.click(within(detail).getByRole("button", { name: "Verify this output" }));
  await waitFor(() => expect(mutationBodies).toEqual([{ decision: "verify", expected_version: 12 }]));
  await waitFor(() => expect(fileReads).toBeGreaterThan(1));
});

test("output_verification_handles_changed_output_and_access_loss_without_leaking_private_state", async () => {
  const output = { id: "file_exact", mission_id: "mission_private", kind: "output", name: "Close report.pdf", media_type: "application/pdf", size: 4200, sha256: "b".repeat(64), state: "awaiting_verification", version: 2, parent_output_id: null, producer_id: "agent_exact", producer: { id: "agent_exact", display_name: "Fin" }, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true, allowed_actions: ["verify_output"], action_targets: { verify_output: { kind: "output", id: "deliverable_exact", revision: 12 } } };
  let status = 409;
  let reads = 0;
  const denied = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/verify")) return response({ detail: { message: "private/path" } }, status);
    if (path.includes("/files?")) { reads += 1; return response({ items: [output] }); }
    throw new Error(`Unexpected request ${path}`);
  });
  window.history.replaceState({}, "", "/missions/mission_private/files?output=file_exact&action=verify_output");
  render(<MissionFiles missionId="mission_private" onAccessLost={denied} />);
  let detail = await screen.findByRole("dialog", { name: "File details" });
  fireEvent.click(within(detail).getByRole("button", { name: "Verify this output" }));
  expect(await within(detail).findByRole("alert")).toHaveTextContent("changed");
  expect(reads).toBeGreaterThan(1);
  expect(document.body.textContent).not.toContain("private/path");

  status = 403;
  detail = screen.getByRole("dialog", { name: "File details" });
  fireEvent.click(within(detail).getByRole("button", { name: "Verify this output" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByText("Close report.pdf")).not.toBeInTheDocument();
  expect(denied).toHaveBeenCalled();
});
