import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { Snapshot } from "../api";
import { RightPanel } from "./RightPanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("right_panel_exchanges_preview_proof_before_nested_asset_iframe_load", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ exchange_id: "one", exchange_proof: "body-only", preview_origin: "https://preview.example.test" }), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  const snapshot = {
    project: { id: "mission_app", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: [], rows: [], row_count: 0 },
  } as unknown as Snapshot;
  render(<RightPanel snapshot={snapshot} tab="preview" onTab={() => undefined} onRefresh={() => undefined} onDeploy={() => undefined} busy={false} />);
  const frame = await screen.findByTitle("App preview");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(frame).toHaveAttribute("src", "https://preview.example.test/projects/mission_app/preview/");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-forms allow-same-origin");
  expect(screen.queryByText("body-only")).not.toBeInTheDocument();
});

test("right_panel_access_loss_clears_retained_preview_and_data", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: { message: "private state" } }), { status: 403, headers: { "Content-Type": "application/json" } }));
  const onAccessLost = vi.fn();
  const snapshot = {
    project: { id: "mission_app", gates_status: "pass", deployed: false },
    preview_url: null,
    preview_data: { columns: ["private total"], rows: [["$42"]], row_count: 1 },
  } as unknown as Snapshot;
  const view = render(<RightPanel snapshot={snapshot} tab="preview" onTab={() => undefined} onRefresh={() => undefined} onDeploy={() => undefined} busy={false} onAccessLost={onAccessLost} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByTitle("App preview")).not.toBeInTheDocument();
  view.rerender(<RightPanel snapshot={snapshot} tab="data" onTab={() => undefined} onRefresh={() => undefined} onDeploy={() => undefined} busy={false} onAccessLost={onAccessLost} />);
  expect(screen.queryByText("$42")).not.toBeInTheDocument();
  expect(onAccessLost).toHaveBeenCalled();
});
