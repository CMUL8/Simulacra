import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  ApiError,
  fetchMissionFileContent,
  listMissionFiles,
  verifyMissionDeliverable,
  type WorkActionTarget,
  type MissionFileItem,
} from "../../../api";
import { FilePreview } from "./FilePreview";
import "./files.css";

const sections: Array<{ id: MissionFileItem["kind"]; label: string }> = [
  { id: "source", label: "Sources" },
  { id: "output", label: "Outputs" },
  { id: "evidence", label: "Evidence" },
];

function isAccessLoss(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function publicState(file: MissionFileItem): string {
  if (file.kind === "source") return "Available";
  if (file.kind === "evidence") return "Recorded";
  const state = file.state.trim().toLowerCase();
  if (state === "draft") return "Draft";
  if (state === "validated") return "Validated";
  if (state === "awaiting_verification") return "Awaiting verification";
  if (state === "verified") return "Verified";
  if (state === "changes_requested") return "Changes requested";
  if (state === "published") return "Published";
  return "In progress";
}

function displayName(actor: MissionFileItem["producer"]): string {
  return actor?.display_name?.trim() || "Mission agent";
}

function parentOutputId(file: MissionFileItem): string | null {
  const value = (file as MissionFileItem & { parent_output_id?: unknown }).parent_output_id;
  return typeof value === "string" && value ? value : null;
}

function verificationTarget(file: MissionFileItem): WorkActionTarget | null {
  if (!file.allowed_actions?.includes("verify_output")) return null;
  const target = file.action_targets?.verify_output;
  if (!target || target.kind !== "output" || typeof target.id !== "string" || !target.id || !Number.isInteger(target.revision)) return null;
  return target;
}

function trapDialogFocus(event: KeyboardEvent<HTMLElement>, onClose: () => void) {
  if (event.key === "Escape") {
    event.preventDefault();
    onClose();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

type SelectedFile = { item: MissionFileItem; opener: HTMLElement | null };

export function MissionFiles({ missionId, previewEnabled = true, onOpenMessage, onAccessLost }: {
  missionId: string;
  previewEnabled?: boolean;
  onOpenMessage?: (messageId: string) => void;
  onAccessLost?: () => void;
}) {
  const [files, setFiles] = useState<MissionFileItem[]>([]);
  const [kind, setKind] = useState<MissionFileItem["kind"]>("source");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accessLost, setAccessLost] = useState(false);
  const [selected, setSelected] = useState<SelectedFile | null>(null);
  const [preview, setPreview] = useState<MissionFileItem | null>(null);
  const [previewRestoreFocus, setPreviewRestoreFocus] = useState<HTMLElement | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [verificationFeedback, setVerificationFeedback] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [verificationPending, setVerificationPending] = useState(false);
  const detailClose = useRef<HTMLButtonElement>(null);
  const loadSequence = useRef(0);

  const handleAccessLost = useCallback(() => {
    loadSequence.current += 1;
    setFiles([]);
    setSelected(null);
    setPreview(null);
    setDownloadError(null);
    setVerificationFeedback(null);
    setVerificationPending(false);
    setAccessLost(true);
    setLoading(false);
    onAccessLost?.();
  }, [onAccessLost]);

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError(null);
    try {
      const result = await listMissionFiles(missionId, "all");
      if (sequence !== loadSequence.current) return;
      setFiles(result.items);
      setSelected((current) => {
        if (!current) return null;
        const latest = result.items.find((item) => item.id === current.item.id);
        return latest ? { ...current, item: latest } : null;
      });
      setAccessLost(false);
    } catch (requestError) {
      if (sequence !== loadSequence.current) return;
      if (isAccessLoss(requestError)) handleAccessLost();
      else setError("Mission files could not be loaded. Try again.");
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [handleAccessLost, missionId]);

  useEffect(() => {
    void load();
    return () => { loadSequence.current += 1; };
  }, [load]);

  useEffect(() => {
    const outputId = new URL(window.location.href).searchParams.get("output");
    if (!outputId) return;
    const output = files.find((file) => file.kind === "output" && file.id === outputId);
    if (output) {
      setKind("output");
      setSelected((current) => current?.item.id === output.id ? current : { item: output, opener: null });
    }
  }, [files]);

  useEffect(() => {
    if (!selected) return;
    detailClose.current?.focus();
    return () => selected.opener?.focus();
  }, [selected]);

  useEffect(() => {
    const syncPreviewRoute = () => {
      const fileId = new URL(window.location.href).searchParams.get("preview");
      if (!fileId || !previewEnabled) {
        setPreview(null);
        return;
      }
      const item = files.find((candidate) => candidate.id === fileId && candidate.previewable);
      if (item) setPreview(item);
    };
    syncPreviewRoute();
    window.addEventListener("popstate", syncPreviewRoute);
    return () => window.removeEventListener("popstate", syncPreviewRoute);
  }, [files, previewEnabled]);

  const counts = useMemo(() => new Map(sections.map((section) => [section.id, files.filter((file) => file.kind === section.id).length])), [files]);
  const visible = useMemo(() => files.filter((file) => file.kind === kind), [files, kind]);
  const versions = useMemo(() => {
    if (!selected || selected.item.kind !== "output") return [];
    return files.filter((file) => file.kind === "output" && file.name === selected.item.name).sort((left, right) => right.version - left.version);
  }, [files, selected]);
  const evidence = useMemo(() => selected?.item.kind === "output"
    ? files.filter((file) => file.kind === "evidence" && parentOutputId(file) === selected.item.id)
    : [], [files, selected]);
  const sources = useMemo(() => new Map(files.filter((file) => file.kind === "source").map((file) => [file.id, file])), [files]);

  const openPreview = (file: MissionFileItem) => {
    if (!previewEnabled) return;
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPreviewRestoreFocus(selected?.opener || active);
    setSelected(null);
    const mobile = window.innerWidth <= 768 || Boolean(window.matchMedia?.("(max-width: 48rem)").matches);
    if (mobile) {
      const query = new URLSearchParams(window.location.search);
      query.set("preview", file.id);
      window.history.pushState({ ...window.history.state, missionFilePreview: true }, "", `${window.location.pathname}?${query.toString()}`);
    }
    setPreview(file);
  };

  const closeDetails = () => {
    setSelected(null);
    const query = new URLSearchParams(window.location.search);
    if (!query.has("output")) return;
    query.delete("output");
    const suffix = query.toString();
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  const closePreview = () => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("preview") === preview?.id) {
      if (window.history.state?.missionFilePreview) {
        window.history.back();
        return;
      }
      query.delete("preview");
      const suffix = query.toString();
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
    setPreview(null);
  };

  const download = async (file: MissionFileItem) => {
    setDownloadError(null);
    try {
      const blob = await fetchMissionFileContent(missionId, file.id, "attachment");
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = file.name;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (requestError) {
      if (isAccessLoss(requestError)) handleAccessLost();
      else setDownloadError("Download is temporarily unavailable. Try again.");
    }
  };

  const verifyOutput = async (file: MissionFileItem) => {
    const target = verificationTarget(file);
    if (!target || verificationPending) return;
    setVerificationPending(true);
    setVerificationFeedback(null);
    try {
      await verifyMissionDeliverable(missionId, target.id, target.revision);
      setVerificationFeedback({ kind: "success", message: "Output verified." });
      await load();
    } catch (requestError) {
      if (isAccessLoss(requestError)) {
        handleAccessLost();
        return;
      }
      if (requestError instanceof ApiError && requestError.status === 409) {
        await load();
        setVerificationFeedback({ kind: "error", message: "This output changed. We refreshed the exact candidate and evidence for another review." });
      } else {
        setVerificationFeedback({ kind: "error", message: "Verification could not be saved. The output remains awaiting review." });
      }
    } finally {
      setVerificationPending(false);
    }
  };

  if (accessLost) return <div className="files-state is-error" role="alert"><strong>You no longer have access to this Mission.</strong><span>Return to Missions to continue with files you can access.</span></div>;

  return <section className="mission-files" aria-label="Mission Files">
    <header className="mission-files-header"><div><p className="workplace-eyebrow">Files</p><h2>Sources, outputs, and evidence</h2><p>Every deliverable stays connected to its inputs, review evidence, and human verification.</p></div></header>
    <div className="file-kind-tabs" role="tablist" aria-label="File types">
      {sections.map((section) => <button key={section.id} type="button" role="tab" aria-selected={kind === section.id} onClick={() => { setKind(section.id); setSelected(null); }}>{section.label} <span>{counts.get(section.id) || 0}</span></button>)}
    </div>
    {loading ? <div className="files-state" role="status">Loading Missions files…</div> : error ? <div className="files-state is-error" role="alert"><strong>{error}</strong><button type="button" onClick={() => void load()}>Retry</button></div> : <div className="file-list">
      {visible.map((file) => <article className="file-row" key={file.id}>
        <button className="file-row-main" type="button" aria-label={`Open ${file.name} details`} onClick={(event) => setSelected({ item: file, opener: event.currentTarget })}>
          <span className="file-type" aria-hidden="true">{file.name.split(".").pop()?.slice(0, 4).toUpperCase() || "FILE"}</span>
          <span className="file-row-copy"><strong>{file.name}</strong><small>{file.media_type} · {humanSize(file.size)}{file.kind !== "source" ? ` · Version ${file.version}` : ""}</small></span>
          <span className={`file-status is-${file.state}`}>{publicState(file)}</span>
          <span className="file-provenance">{file.kind === "output" ? `Produced by ${displayName(file.producer)}` : file.kind === "evidence" ? "Review evidence" : "Mission source"}</span>
          {file.state.toLowerCase() === "verified" ? <span className="file-verified">Verified by {file.verifier?.display_name?.trim() || "a human"}</span> : null}
        </button>
        <div className="file-row-actions">
          {previewEnabled && file.previewable ? <button type="button" onClick={() => openPreview(file)}>Preview</button> : null}
          {file.downloadable ? <button type="button" onClick={() => void download(file)}>Download</button> : null}
        </div>
      </article>)}
      {!visible.length ? <div className="files-state"><strong>No {sections.find((section) => section.id === kind)?.label.toLowerCase()} yet.</strong><span>{kind === "source" ? "Add source material to begin this Mission." : kind === "output" ? "Agent outputs will appear here for human review." : "Evidence appears as outputs are checked and verified."}</span></div> : null}
    </div>}
    {downloadError ? <p className="files-download-error" role="alert">{downloadError}</p> : null}

    {selected && !preview ? <>
      <button className="file-detail-scrim" type="button" aria-label="Close file details backdrop" onClick={closeDetails} />
      <aside className="file-detail" role="dialog" aria-modal="true" aria-label="File details" onKeyDown={(event) => trapDialogFocus(event, closeDetails)}>
      <header><div><p className="workplace-eyebrow">{publicState(selected.item)}</p><h3>{selected.item.name}</h3></div><button ref={detailClose} type="button" aria-label="Close file details" onClick={closeDetails}>Close</button></header>
      <dl><div><dt>Version</dt><dd>{selected.item.version}</dd></div><div><dt>Type</dt><dd>{selected.item.media_type}</dd></div><div><dt>Size</dt><dd>{humanSize(selected.item.size)}</dd></div>{selected.item.producer ? <div><dt>Produced by</dt><dd>{displayName(selected.item.producer)}</dd></div> : null}</dl>
      {selected.item.kind === "output" ? <>
        <section className="file-sources-section"><h4>Sources</h4>{selected.item.source_ids.length ? <ul>{selected.item.source_ids.map((sourceId) => {
          const source = sources.get(sourceId);
          return <li key={sourceId}>{source ? <button type="button" aria-label={`Open source ${source.name}`} onClick={() => { setKind("source"); setSelected({ item: source, opener: selected.opener }); }}>{source.name}</button> : <span>Source unavailable</span>}</li>;
        })}</ul> : <p>No source files are linked to this output.</p>}</section>
        <section className="file-evidence-section"><h4>Evidence</h4>{evidence.length ? evidence.map((item) => <div key={item.id}><strong>{item.name}</strong><span>{publicState(item)} · Version {item.version}</span></div>) : <p>No review evidence has been attached yet.</p>}</section>
        {versions.length > 1 ? <section><h4>Version history</h4><ol className="file-version-list">{versions.map((version) => <li key={version.id}><span>Version {version.version}</span><span>{publicState(version)}</span></li>)}</ol></section> : null}
        <section className="file-verification-section"><h4>Human decision</h4><p>{selected.item.state.toLowerCase() === "verified" ? `Verified by ${selected.item.verifier?.display_name?.trim() || "a human"}.` : "Confirm this exact version only after reviewing its sources and evidence above."}</p>
          {verificationTarget(selected.item) ? <button className="file-verify-action" type="button" disabled={verificationPending} onClick={() => void verifyOutput(selected.item)}>{verificationPending ? "Verifying output…" : "Verify this output"}</button> : null}
          {verificationFeedback ? <p className={`file-verification-feedback is-${verificationFeedback.kind}`} role={verificationFeedback.kind === "error" ? "alert" : "status"}>{verificationFeedback.message}</p> : null}
        </section>
      </> : null}
      {selected.item.introduced_by_message_id && onOpenMessage ? <button className="file-original-message" type="button" onClick={() => onOpenMessage(selected.item.introduced_by_message_id!)}>Open original message</button> : null}
      <div className="file-detail-actions">{previewEnabled && selected.item.previewable ? <button type="button" onClick={() => openPreview(selected.item)}>Preview</button> : null}{selected.item.downloadable ? <button type="button" onClick={() => void download(selected.item)}>Download</button> : null}</div>
      </aside>
    </> : null}
    {preview && previewEnabled ? <>
      <button className="file-detail-scrim is-preview" type="button" aria-label="Close file preview backdrop" onClick={closePreview} />
      <FilePreview missionId={missionId} file={preview} dedicatedPreviewEnabled={previewEnabled} restoreFocusTo={previewRestoreFocus} onClose={closePreview} onAccessLost={handleAccessLost} />
    </> : null}
  </section>;
}
