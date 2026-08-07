import { ExternalLink, LayoutGrid, RefreshCw, Rocket, Table2 } from "lucide-react";
import { useState } from "react";
import type { Snapshot } from "../api";
import { Tooltip } from "./ui/Tooltip";

export type RightTab = "preview" | "data";

type Props = {
  snapshot: Snapshot | null;
  tab: RightTab;
  onTab: (t: RightTab) => void;
  onRefresh: () => void;
  onDeploy: () => void;
  busy: boolean;
};

const TABS: { id: RightTab; label: string; icon: typeof LayoutGrid }[] = [
  { id: "preview", label: "Preview", icon: LayoutGrid },
  { id: "data", label: "Data", icon: Table2 },
];

export function RightPanel({ snapshot, tab, onTab, onRefresh, onDeploy, busy }: Props) {
  const project = snapshot?.project;
  const previewUrl = snapshot?.preview_url;
  const [frameKey, setFrameKey] = useState(0);

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
              <Tooltip label="Open in new tab">
                <a href={previewUrl} target="_blank" rel="noreferrer" className="tool-btn">
                  <ExternalLink size={14} />
                </a>
              </Tooltip>
            </>
          )}
          {project && (
            <Tooltip label={project.deployed ? "Already deployed" : "Approve and deploy app"}>
              <button
                type="button"
                className="deploy-btn"
                disabled={busy || project.gates_status !== "pass" || project.deployed}
                onClick={onDeploy}
              >
                <Rocket size={14} />
                {project.deployed ? "Deployed" : "Deploy"}
              </button>
            </Tooltip>
          )}
        </div>
      </header>

      <div className="panel-body">
        {!project && (
          <div className="panel-empty">
            <LayoutGrid size={32} strokeWidth={1.25} className="panel-empty-icon" />
            <h2>App preview</h2>
            <p>Build a project to generate your internal data app from the data room.</p>
          </div>
        )}

        {project && tab === "preview" && (
          previewUrl ? (
            <div className="preview-shell">
              <div className="preview-chrome">
                <span className="chrome-dot" />
                <span className="chrome-dot" />
                <span className="chrome-dot" />
                <span className="chrome-url">{previewUrl}</span>
              </div>
              <iframe key={`${previewUrl}-${frameKey}`} src={previewUrl} title="App preview" className="preview-frame" />
            </div>
          ) : (
            <div className="panel-empty"><p>Preview will appear after build completes.</p></div>
          )
        )}

        {project && tab === "data" && (
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
            <p className="table-foot">{snapshot?.preview_data.row_count ?? 0} rows · DuckDB</p>
          </div>
        )}
      </div>
    </section>
  );
}
