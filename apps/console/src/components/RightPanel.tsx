import { ExternalLink, LayoutGrid, RefreshCw, Rocket, Table2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, exchangeMissionPreview, type Snapshot } from "../api";
import { Tooltip } from "./ui/Tooltip";

export type RightTab = "preview" | "data";

type Props = {
  snapshot: Snapshot | null;
  tab: RightTab;
  onTab: (t: RightTab) => void;
  onRefresh: () => void;
  onDeploy: () => void;
  busy: boolean;
  previewEnabled?: boolean;
  onAccessLost?: () => void;
};

const TABS: { id: RightTab; label: string; icon: typeof LayoutGrid }[] = [
  { id: "preview", label: "Preview", icon: LayoutGrid },
  { id: "data", label: "Data", icon: Table2 },
];

export function RightPanel({ snapshot, tab, onTab, onRefresh, onDeploy, busy, previewEnabled = true, onAccessLost }: Props) {
  const project = snapshot?.project;
  const [frameKey, setFrameKey] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [accessLost, setAccessLost] = useState(false);

  useEffect(() => {
    if (!project?.id || !previewEnabled) {
      setPreviewUrl(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewUrl(null);
    setPreviewError(null);
    setAccessLost(false);
    setPreviewLoading(true);
    void exchangeMissionPreview(project.id, controller.signal)
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
  }, [frameKey, onAccessLost, previewEnabled, project?.id]);

  return (
    <section className="right-panel">
      <header className="panel-toolbar">
        <div className="tabs">
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                type="button"
                className={`tab ${tab === t.id ? "active" : ""}`}
                onClick={() => onTab(t.id)}
              >
                <Icon size={14} strokeWidth={1.75} />
                {t.label}
              </button>
            );
          })}
        </div>
        <div className="toolbar-actions">
          {previewUrl && tab === "preview" && (
            <>
              <Tooltip label="Refresh preview">
                <button
                  type="button"
                  className="tool-btn"
                  onClick={() => {
                    setFrameKey((k) => k + 1);
                    onRefresh();
                  }}
                >
                  <RefreshCw size={14} />
                </button>
              </Tooltip>
              {previewUrl ? <Tooltip label="Open verified preview in a new tab">
                <a href={previewUrl} target="_blank" rel="noreferrer" className="tool-btn">
                  <ExternalLink size={14} />
                </a>
              </Tooltip> : null}
            </>
          )}
          {project && (
            <Tooltip label={project.deployed ? "Already published" : "Approve and publish output"}>
              <button
                type="button"
                className="deploy-btn"
                disabled={busy || project.gates_status !== "pass" || project.deployed}
                onClick={onDeploy}
              >
                <Rocket size={14} />
                {project.deployed ? "Published" : "Publish"}
              </button>
            </Tooltip>
          )}
        </div>
      </header>

      <div className="panel-body">
        {!project && (
          <div className="panel-empty">
            <LayoutGrid size={32} strokeWidth={1.25} className="panel-empty-icon" />
            <h2>Output preview</h2>
            <p>Set a Mission to create a verified output from your source files.</p>
          </div>
        )}

        {project && tab === "preview" && (
          previewUrl ? (
            <div className="preview-shell">
              <div className="preview-chrome">
                <span className="chrome-dot" />
                <span className="chrome-dot" />
                <span className="chrome-dot" />
                <span className="chrome-url">Verified preview</span>
              </div>
              <iframe key={`${previewUrl}-${frameKey}`} src={previewUrl} title="App preview" className="preview-frame" sandbox="allow-scripts allow-forms allow-same-origin" />
            </div>
          ) : (
            <div className="panel-empty"><p role={previewError ? "alert" : "status"}>{previewError || (previewLoading ? "Preparing a secure preview…" : "Preview will appear after the output is ready.")}</p></div>
          )
        )}

        {project && tab === "data" && !accessLost && (
          <div className="data-table-wrap">
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
    </section>
  );
}
