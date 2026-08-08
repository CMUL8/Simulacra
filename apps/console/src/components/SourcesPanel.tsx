import { Database, FilePlus2, RefreshCw, Trash2, X } from "lucide-react";
import { useRef } from "react";
import type { DataProfile, DataRoomFile } from "../api";
import { FileTypeIcon } from "./FileTypeIcon";

type Props = {
  open: boolean;
  busy?: boolean;
  files: DataRoomFile[];
  pendingFiles?: File[];
  fixtureAttached?: boolean;
  profile?: DataProfile | null;
  extractErrors?: string[];
  extractSkipped?: string[];
  mode: "landing" | "project";
  onClose: () => void;
  onToggleFixture?: () => void;
  onPickFiles?: (files: File[]) => void;
  onClearPending?: (name: string) => void;
  onRemoveSource?: (name: string) => void;
  onSeedFixtures?: () => void;
  onReingest?: () => void;
};

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function SourcesPanel({
  open,
  busy,
  files,
  pendingFiles = [],
  fixtureAttached,
  profile,
  extractErrors = [],
  extractSkipped = [],
  mode,
  onClose,
  onToggleFixture,
  onPickFiles,
  onClearPending,
  onRemoveSource,
  onSeedFixtures,
  onReingest,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  if (!open) return null;

  const nuances = profile?.nuance_notes?.slice(0, 4) || [];

  return (
    <div className="sources-overlay" role="dialog" aria-label="Data sources">
      <div className="sources-panel">
        <header className="sources-head">
          <div>
            <h2>
              <Database size={16} strokeWidth={1.75} /> Data sources
            </h2>
            <p>Ingest files the app and builder will design around.</p>
          </div>
          <button type="button" className="sources-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>

        <div className="sources-body">
          {mode === "landing" && (
            <label className="sources-fixture">
              <input
                type="checkbox"
                checked={!!fixtureAttached}
                disabled={busy}
                onChange={() => onToggleFixture?.()}
              />
              <span>
                Include sample diligence pack
                <em>vendor research · notes · supplement CSV</em>
              </span>
            </label>
          )}

          <div className="sources-actions">
            <button
              type="button"
              className="sources-btn"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              <FilePlus2 size={14} />
              Upload files
            </button>
            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              accept=".md,.txt,.csv,.json,.pdf,.xlsx"
              onChange={(e) => {
                const list = Array.from(e.target.files || []);
                if (list.length) onPickFiles?.(list);
                e.target.value = "";
              }}
            />
            {mode === "project" && (
              <>
                <button type="button" className="sources-btn" disabled={busy} onClick={() => onSeedFixtures?.()}>
                  Add sample pack
                </button>
                <button type="button" className="sources-btn" disabled={busy} onClick={() => onReingest?.()}>
                  <RefreshCw size={14} />
                  Re-ingest
                </button>
              </>
            )}
          </div>

          <p className="sources-hint">
            Extractable: .md .txt .csv .json · Max 8&nbsp;MB each · Other types stay in inventory
          </p>

          {pendingFiles.length > 0 && (
            <section>
              <h3>Pending upload</h3>
              <ul className="sources-list">
                {pendingFiles.map((f) => (
                  <li key={f.name + f.size}>
                    <FileTypeIcon ext={f.name.split(".").pop() || "bin"} />
                    <div className="sources-meta">
                      <strong>{f.name}</strong>
                      <span>{fmtSize(f.size)} · queued</span>
                    </div>
                    <button
                      type="button"
                      className="sources-icon-btn"
                      disabled={busy}
                      aria-label={`Remove ${f.name}`}
                      onClick={() => onClearPending?.(f.name)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3>
              {mode === "landing" ? "Sample pack" : "In data room"}{" "}
              <span>{files.length}</span>
            </h3>
            {files.length === 0 ? (
              <p className="sources-empty">No files yet — upload or attach the sample pack.</p>
            ) : (
              <ul className="sources-list">
                {files.map((f) => (
                  <li key={f.name}>
                    <FileTypeIcon ext={f.type || f.name.split(".").pop() || "bin"} />
                    <div className="sources-meta">
                      <strong>{f.name}</strong>
                      <span>
                        {fmtSize(f.size)}
                        {f.status ? ` · ${f.status}` : ""}
                        {typeof f.row_count === "number" && f.row_count > 0
                          ? ` · ${f.row_count} rows`
                          : ""}
                      </span>
                      {f.detail && <em>{f.detail}</em>}
                    </div>
                    {mode === "project" && onRemoveSource && (
                      <button
                        type="button"
                        className="sources-icon-btn"
                        disabled={busy}
                        aria-label={`Remove ${f.name}`}
                        onClick={() => onRemoveSource(f.name)}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {profile && !profile.empty_room && (
            <section className="sources-profile">
              <h3>Data profile</h3>
              <p>
                {profile.row_count ?? 0} findings
                {profile.high_risk != null ? ` · ${profile.high_risk} high` : ""}
                {profile.vendors?.length ? ` · ${profile.vendors.length} vendors` : ""}
              </p>
              {nuances.length > 0 && (
                <ul>
                  {nuances.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {(extractErrors.length > 0 || extractSkipped.length > 0) && (
            <section className="sources-warnings">
              {extractErrors.length > 0 && (
                <>
                  <h3>Extract errors</h3>
                  <ul>
                    {extractErrors.slice(0, 5).map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </>
              )}
              {extractSkipped.length > 0 && (
                <>
                  <h3>Skipped</h3>
                  <ul>
                    {extractSkipped.slice(0, 5).map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
