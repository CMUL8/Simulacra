import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { Snapshot } from "../api";
import { PreviewDrawer } from "./PreviewDrawer";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("preview_drawer_exchanges_body_only_proof_then_uses_token_free_cross_origin_iframe", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ exchange_id: "exchange_secret", exchange_proof: "proof_secret", preview_origin: "https://preview.example.test" }), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  const snapshot = {
    project: { id: "mission_close", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: [], rows: [], row_count: 0 },
  } as unknown as Snapshot;
  render(<PreviewDrawer open snapshot={snapshot} onClose={() => undefined} onRefresh={() => undefined} />);
  const frame = await screen.findByTitle("App preview");
  expect(fetcher).toHaveBeenNthCalledWith(2, "https://preview.example.test/preview/exchange", expect.objectContaining({
    method: "POST",
    credentials: "include",
    body: JSON.stringify({ exchange_id: "exchange_secret", exchange_proof: "proof_secret" }),
  }));
  expect(frame).toHaveAttribute("src", "https://preview.example.test/projects/mission_close/preview/");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-forms allow-same-origin");
  expect(document.body.textContent).not.toContain("proof_secret");
  expect(frame.getAttribute("src")).not.toContain("exchange");
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
});

test("preview drawer clears protected content when Mission access is lost", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: { message: "private/path" } }), { status: 403, headers: { "Content-Type": "application/json" } }));
  const onAccessLost = vi.fn();
  const snapshot = {
    project: { id: "mission_close", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: [], rows: [], row_count: 0 },
  } as unknown as Snapshot;
  render(<PreviewDrawer open snapshot={snapshot} onClose={() => undefined} onRefresh={() => undefined} onAccessLost={onAccessLost} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByTitle("App preview")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("private/path");
  expect(onAccessLost).toHaveBeenCalled();
});

test("preview_access_loss_clears_retained_snapshot_data", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ exchange_id: "one", exchange_proof: "proof", preview_origin: "https://preview.example.test" }), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { message: "private state" } }), { status: 403, headers: { "Content-Type": "application/json" } }));
  const snapshot = {
    project: { id: "mission_close", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: ["private total"], rows: [["$42"]], row_count: 1 },
  } as unknown as Snapshot;
  const onAccessLost = vi.fn();
  const view = render(<PreviewDrawer open snapshot={snapshot} onClose={() => undefined} onRefresh={() => undefined} refreshToken={0} onAccessLost={onAccessLost} />);
  expect(await screen.findByTitle("App preview")).toBeInTheDocument();
  view.rerender(<PreviewDrawer open snapshot={snapshot} onClose={() => undefined} onRefresh={() => undefined} refreshToken={1} onAccessLost={onAccessLost} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByTitle("App preview")).not.toBeInTheDocument();
  expect(screen.queryByText("$42")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(3);
});

test("preview_flag_off_does_not_request_or_render_a_preview", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch");
  const snapshot = {
    project: { id: "mission_close", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: [], rows: [], row_count: 0 },
  } as unknown as Snapshot;
  render(<PreviewDrawer open snapshot={snapshot} previewEnabled={false} onClose={() => undefined} onRefresh={() => undefined} />);
  expect(screen.getByText("Preview unavailable")).toBeInTheDocument();
  expect(screen.queryByTitle("App preview")).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
