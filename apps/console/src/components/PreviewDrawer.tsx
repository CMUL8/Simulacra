import { ExternalLink, PanelRightClose, RefreshCw, Table2 } from "lucide-react";
import { useState } from "react";
import type { Snapshot } from "../api";

type Tab = "preview" | "data";

type Props = {
  open: boolean;
  snapshot: Snapshot | null;
  onClose: () => void;
  onRefresh: () => void;
};

export function PreviewDrawer({ open, snapshot, onClose, onRefresh }: Props) {
  const [tab, setTab] = useState<Tab>("preview");
  const [frameKey, setFrameKey] = useState(0);
  const previewUrl = snapshot?.preview_url;

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
          {tab === "preview" && (
            previewUrl ? (
              <iframe
                key={`${previewUrl}-${frameKey}`}
                src={previewUrl}
                title="App preview"
                className="preview-drawer-frame"
              />
            ) : (
              <div className="preview-drawer-empty">
                <p>Preview will appear after build completes.</p>
              </div>
            )
          )}
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
