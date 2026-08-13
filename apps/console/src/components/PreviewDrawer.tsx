import { ExternalLink, PanelRightClose, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Snapshot } from "../api";

type Tab = "preview" | "data";

type Props = {
  open: boolean;
  snapshot: Snapshot | null;
  onClose: () => void;
  onRefresh: () => void;
  onDeploy?: () => void;
  busy?: boolean;
  refreshToken?: number;
};

/** Same-origin preview path (no localhost). */
export function resolvePreviewSrc(raw: string | null | undefined, bust = 0): string | null {
  if (!raw) return null;
  if (/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?/i.test(raw)) return null;
  const apiBase = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "http://127.0.0.1:8000");
  let href: string;
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    href = raw;
  } else {
    const base = String(apiBase).replace(/\/$/, "");
    const origin =
      base === "" || base === "/api"
        ? import.meta.env.PROD
          ? ""
          : "http://127.0.0.1:8000"
        : base;
    href = `${origin}${raw.startsWith("/") ? raw : `/${raw}`}`;
  }
  const u = new URL(href, typeof window !== "undefined" ? window.location.origin : "http://localhost");
  u.searchParams.set("v", String(bust || Date.now() % 1_000_000));
  return u.toString();
}

function displayUrl(href: string): string {
  try {
    const u = new URL(href);
    const path = `${u.pathname}${u.search}`.replace(/[?&]v=[^&]+/, "").replace(/[?&]$/, "");
    const host = u.host && u.host !== window.location.host ? u.host : "";
    const shown = host ? `${host}${path}` : path || "/";
    if (shown.length <= 52) return shown;
    return `${shown.slice(0, 28)}…${shown.slice(-20)}`;
  } catch {
    return href;
  }
}

/** Preview as a workspace pane — not a modal overlay. */
export function PreviewDrawer({
  open,
  snapshot,
  onClose,
  onRefresh,
  onDeploy,
  busy,
  refreshToken = 0,
}: Props) {
  const [tab, setTab] = useState<Tab>("preview");
  const [frameKey, setFrameKey] = useState(0);
  const project = snapshot?.project;
  const canDeploy =
    Boolean(onDeploy) &&
    project?.gates_status === "pass" &&
    !project?.deployed;
  const columns = snapshot?.preview_data.columns ?? [];
  const rows = snapshot?.preview_data.rows ?? [];
  const hasData = columns.length > 0;

  useEffect(() => {
    if (refreshToken) setFrameKey((k) => k + 1);
  }, [refreshToken]);

  const previewUrl = useMemo(
    () => resolvePreviewSrc(snapshot?.preview_url, frameKey + refreshToken),
    [snapshot?.preview_url, frameKey, refreshToken],
  );

  if (!open) return null;

  const emptyCopy = snapshot?.preview_url?.includes("127.0.0.1")
    ? "This project still points at an old local preview. Start a new project or hit Build again."
    : hasData
      ? "Preview appears here when the draft is ready."
      : "Sources aren't loaded yet. Add data, then send a message — the preview fills in here.";

  return (
    <aside className="preview-pane" aria-label="Preview">
      <header className="preview-drawer-head">
        <div className="preview-address">
          {previewUrl ? (
            <button
              type="button"
              className="preview-address-url"
              title="Copy preview URL"
              onClick={() => {
                const abs = previewUrl.startsWith("http")
                  ? previewUrl
                  : `${window.location.origin}${previewUrl}`;
                void navigator.clipboard?.writeText(abs);
              }}
            >
              {displayUrl(previewUrl)}
            </button>
          ) : (
            <span className="preview-address-url mute">No preview yet</span>
          )}
        </div>
        <div className="preview-drawer-actions">
          {hasData ? (
            <button
              type="button"
              className={`preview-data-toggle${tab === "data" ? " on" : ""}`}
              onClick={() => setTab((t) => (t === "data" ? "preview" : "data"))}
            >
              Data
            </button>
          ) : null}
          {canDeploy && (
            <button
              type="button"
              className="composer-action emphasis preview-deploy-btn"
              disabled={busy}
              onClick={onDeploy}
              title="Approve this preview for your team"
              aria-label="Ship — approve this preview for your team"
            >
              Ship
            </button>
          )}
          {project?.deployed && (
            <span className="chrome-chip" title="Approved share link">
              Shipped
            </span>
          )}
          {previewUrl && tab === "preview" && (
            <>
              <button
                type="button"
                className="icon-btn"
                onClick={() => {
                  setFrameKey((k) => k + 1);
                  onRefresh();
                }}
                title="Refresh"
              >
                <RefreshCw size={14} strokeWidth={1.5} />
              </button>
              <a href={previewUrl} target="_blank" rel="noreferrer" className="icon-btn" title="Open in tab">
                <ExternalLink size={14} strokeWidth={1.5} />
              </a>
            </>
          )}
          <button type="button" className="icon-btn" onClick={onClose} title="Close preview">
            <PanelRightClose size={14} strokeWidth={1.5} />
          </button>
        </div>
      </header>

      <div className="preview-drawer-body">
        {tab === "preview" &&
          (previewUrl ? (
            <iframe
              key={`${previewUrl}-${frameKey}`}
              src={previewUrl}
              title="App preview"
              className="preview-drawer-frame"
            />
          ) : (
            <div className="preview-drawer-empty">
              <p>{emptyCopy}</p>
            </div>
          ))}
        {tab === "data" && (
          <div className="data-table-wrap drawer-data">
            {hasData ? (
              <>
                <table>
                  <thead>
                    <tr>
                      {columns.map((c) => (
                        <th key={c}>{c.replace(/_/g, " ")}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 100).map((row, i) => (
                      <tr key={i}>
                        {columns.map((c) => (
                          <td key={c} className={c === "risk_level" ? `risk-${row[c]}` : ""}>
                            {String(row[c] ?? "")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="table-foot">{snapshot?.preview_data.row_count ?? 0} rows</p>
              </>
            ) : (
              <div className="preview-drawer-empty">
                <p>No rows yet. They show up here once sources are in the data room.</p>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
