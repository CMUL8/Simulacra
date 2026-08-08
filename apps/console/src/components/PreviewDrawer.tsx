import { ExternalLink, PanelRightClose, RefreshCw, Table2 } from "lucide-react";
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
  /** Bump to force iframe reload after style apply */
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

  useEffect(() => {
    if (refreshToken) setFrameKey((k) => k + 1);
  }, [refreshToken]);

  const previewUrl = useMemo(
    () => resolvePreviewSrc(snapshot?.preview_url, frameKey + refreshToken),
    [snapshot?.preview_url, frameKey, refreshToken],
  );

  if (!open) return null;

  return (
    <div className="preview-drawer-overlay" onClick={onClose}>
      <aside className="preview-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="preview-drawer-head">
          <div className="preview-drawer-tabs">
            <button
              type="button"
              className={tab === "preview" ? "active" : ""}
              onClick={() => setTab("preview")}
            >
              Preview
            </button>
            <button
              type="button"
              className={tab === "data" ? "active" : ""}
              onClick={() => setTab("data")}
            >
              <Table2 size={13} />
              Data
            </button>
          </div>
          <div className="preview-drawer-actions">
            {canDeploy && (
              <button
                type="button"
                className="approve-btn preview-deploy-btn"
                disabled={busy}
                onClick={onDeploy}
              >
                Ship
              </button>
            )}
            {project?.deployed && (
              <span className="source-chip source-prime">Shipped</span>
            )}
            {project?.deployed && previewUrl && (
              <button
                type="button"
                className="ghost-btn quiet"
                onClick={() => {
                  void navigator.clipboard?.writeText(previewUrl);
                }}
                title="Copy share URL"
              >
                Copy link
              </button>
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
                  <RefreshCw size={14} />
                </button>
                <a href={previewUrl} target="_blank" rel="noreferrer" className="icon-btn" title="Open in tab">
                  <ExternalLink size={14} />
                </a>
              </>
            )}
            <button type="button" className="icon-btn" onClick={onClose} title="Close">
              <PanelRightClose size={14} />
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
                <p>
                  {snapshot?.preview_url?.includes("127.0.0.1")
                    ? "This project still points at an old local preview. Start a new project or hit Build app again."
                    : "Preview will appear when the draft is ready."}
                </p>
              </div>
            ))}
          {tab === "data" && (
            <div className="data-table-wrap drawer-data">
              <table>
                <thead>
                  <tr>
                    {(snapshot?.preview_data.columns ?? []).map((c) => (
                      <th key={c}>{c.replace(/_/g, " ")}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(snapshot?.preview_data.rows ?? []).slice(0, 100).map((row, i) => (
                    <tr key={i}>
                      {(snapshot?.preview_data.columns ?? []).map((c) => (
                        <td key={c} className={c === "risk_level" ? `risk-${row[c]}` : ""}>
                          {String(row[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="table-foot">{snapshot?.preview_data.row_count ?? 0} rows</p>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
