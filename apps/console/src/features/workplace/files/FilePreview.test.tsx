import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { MissionFileItem } from "../../../api";
import { FilePreview } from "./FilePreview";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("file_preview_uses_dedicated_origin_sandbox_with_allow_same_origin", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ exchange_id: "one", exchange_proof: "proof", preview_origin: "https://preview.example.test" }), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  const file = {
    id: "file_private",
    mission_id: "mission_app",
    kind: "output",
    name: "Verified app",
    media_type: "text/html",
    size: 1200,
    sha256: "0".repeat(64),
    state: "verified",
    version: 3,
    producer_id: null,
    producer: { id: "agent_private", display_name: "Cody" },
    verifier: { id: "human_private", display_name: "Ada" },
    source_ids: [],
    introduced_by_message_id: null,
    created_at: null,
    updated_at: null,
    previewable: true,
    downloadable: true,
  } satisfies MissionFileItem;
  render(<FilePreview missionId="mission_app" file={file} onClose={() => undefined} />);
  const frame = await screen.findByTitle("Verified app preview");
  expect(frame).toHaveAttribute("src", "https://preview.example.test/projects/mission_app/preview/");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-forms allow-same-origin");
  expect(document.body.textContent).not.toContain("file_private");
});

test.each([
  ["Unverified draft.html", "text/html"],
  ["Adversarial image.svg", "image/svg+xml"],
])("adversarial_inline_%s_renders_as_inert_source_without_a_network_capable_frame", async (name, mediaType) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("<script>top.location='https://evil.example'</script><form action='https://evil.example'></form>", { status: 200, headers: { "Content-Type": mediaType } }));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:passive-file") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const file = {
    id: "file_private", mission_id: "mission_app", kind: "output", name, media_type: mediaType, size: 1200,
    sha256: "0".repeat(64), state: "awaiting_verification", version: 3, producer_id: null, producer: null, verifier: null,
    source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true,
  } satisfies MissionFileItem;
  render(<FilePreview missionId="mission_app" file={file} onClose={() => undefined} dedicatedPreviewEnabled />);
  const source = await screen.findByRole("document", { name: `${name} source` });
  expect(source).toHaveTextContent("top.location='https://evil.example'");
  expect(source.querySelector("script, form, iframe, img, object, embed")).toBeNull();
  expect(screen.queryByTitle(`${name} preview`)).not.toBeInTheDocument();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(globalThis.fetch).toHaveBeenCalledTimes(1);
});

test("preview_focus_trap_includes_the_iframe_and_returns_to_close", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Blob(["hello"], { type: "text/plain" }), { status: 200 }));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const file = { id: "safe_file", mission_id: "mission_app", kind: "source", name: "Read me.txt", media_type: "text/plain", size: 5, sha256: "0".repeat(64), state: "ready", version: 1, producer_id: null, producer: null, verifier: null, source_ids: [], introduced_by_message_id: null, created_at: null, updated_at: null, previewable: true, downloadable: true } satisfies MissionFileItem;
  render(<FilePreview missionId="mission_app" file={file} onClose={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Read me.txt preview" });
  const close = within(dialog).getByRole("button", { name: "Close file preview" });
  const frame = await screen.findByTitle("Read me.txt preview");
  await waitFor(() => expect(close).toHaveFocus());
  fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
  expect(frame).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Tab" });
  expect(close).toHaveFocus();
  const endGuard = dialog.querySelector<HTMLElement>("[data-preview-focus-end]");
  expect(endGuard).not.toBeNull();
  endGuard?.focus();
  expect(close).toHaveFocus();
});

test("markdown_outputs_render_as_a_reviewable_document_without_an_iframe", async () => {
  const markdown = [
    "# Invoice 42 exception report",
    "",
    "The invoice total does not match the purchase order.",
    "",
    "## Evidence",
    "",
    "- `invoice-42.csv`, row 42",
    "- [Purchase order](https://example.test/purchase-order)",
    "",
    "<img src=x onerror=alert('unsafe')>",
    "![Tracking pixel](https://evil.example/pixel)",
    "[Unsafe](javascript:alert('unsafe'))",
  ].join("\n");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(markdown, { status: 200, headers: { "Content-Type": "text/markdown" } }));
  const file = {
    id: "report_42", mission_id: "mission_app", kind: "output", name: "invoice-42-report.md", media_type: "text/markdown", size: markdown.length,
    sha256: "0".repeat(64), state: "awaiting_verification", version: 1, producer_id: null, producer: { id: "agent_fin", display_name: "Fin" }, verifier: null,
    source_ids: ["source_invoice_42"], introduced_by_message_id: "message_42", created_at: null, updated_at: null, previewable: true, downloadable: true,
  } satisfies MissionFileItem;

  render(<FilePreview missionId="mission_app" file={file} onClose={() => undefined} />);

  const document = await screen.findByRole("document", { name: "invoice-42-report.md document" });
  expect(within(document).getByRole("heading", { level: 1, name: "Invoice 42 exception report" })).toBeInTheDocument();
  expect(within(document).getByRole("heading", { level: 2, name: "Evidence" })).toBeInTheDocument();
  expect(within(document).getByText("invoice-42.csv")).toBeInTheDocument();
  expect(within(document).getByRole("link", { name: "Purchase order" })).toHaveAttribute("rel", "noreferrer noopener");
  expect(within(document).getByText("Unsafe").closest("a")).toBeNull();
  expect(within(document).getByText("Image omitted: Tracking pixel")).toBeInTheDocument();
  expect(screen.queryByTitle("invoice-42-report.md preview")).not.toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});
