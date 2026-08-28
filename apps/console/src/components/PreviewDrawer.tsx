import { ExternalLink, PanelRightClose, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, exchangeMissionPreview, type Snapshot } from "../api";

type Tab = "preview" | "data";

type Props = {
  open: boolean;
  snapshot: Snapshot | null;
  onClose: () => void;
  onRefresh: () => void;
  onDeploy?: () => void;
  busy?: boolean;
  refreshToken?: number;
  previewEnabled?: boolean;
  onAccessLost?: () => void;
};

/** Preview as a workspace pane — not a modal overlay. */
export function PreviewDrawer({
  open,
  snapshot,
  onClose,
  onRefresh,
  onDeploy,
  busy,
  refreshToken = 0,
  previewEnabled = true,
  onAccessLost,
}: Props) {
  const [tab, setTab] = useState<Tab>("preview");
  const [frameKey, setFrameKey] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [accessLost, setAccessLost] = useState(false);
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

  useEffect(() => {
    const projectId = snapshot?.project.id;
    if (!open || !projectId || !previewEnabled) {
      setPreviewUrl(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewUrl(null);
    setPreviewError(null);
    setAccessLost(false);
    setPreviewLoading(true);
    void exchangeMissionPreview(projectId, controller.signal)
      .then((session) => { if (!controller.signal.aborted) setPreviewUrl(session.previewUrl); })
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setAccessLost(true);
          onAccessLost?.();
        }
        setPreviewError(error instanceof ApiError && (error.status === 401 || error.status === 403)
          ? "You no longer have access to this Mission."
          : error instanceof ApiError && error.status === 404
            ? "No verified preview yet."
            : "The verified preview is temporarily unavailable.");
      })
      .finally(() => { if (!controller.signal.aborted) setPreviewLoading(false); });
    return () => controller.abort();
  }, [frameKey, onAccessLost, open, previewEnabled, snapshot?.project.id]);

  if (!open) return null;

  const emptyCopy = hasData
      ? "Preview appears here when the draft is ready."
      : "Sources aren't loaded yet. Add data, then send a message — the preview fills in here.";

  return (
    <aside className="preview-pane" aria-label="Preview">
      <header className="preview-drawer-head">
        <div className="preview-address">
          <span className="preview-address-url mute">{previewUrl ? "Verified preview" : previewLoading ? "Preparing preview…" : previewEnabled ? "No preview yet" : "Preview unavailable"}</span>
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
              <a href={previewUrl} target="_blank" rel="noreferrer" className="icon-btn" title="Open verified preview in a new tab">
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
              sandbox="allow-scripts allow-forms allow-same-origin"
            />
          ) : (
            <div className="preview-drawer-empty">
              <p role={previewError ? "alert" : previewLoading ? "status" : undefined}>{previewError || (previewLoading ? "Preparing a secure preview…" : emptyCopy)}</p>
            </div>
          ))}
        {tab === "data" && !accessLost && (
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
              <p>No rows yet. They appear here once the Mission has source data.</p>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
