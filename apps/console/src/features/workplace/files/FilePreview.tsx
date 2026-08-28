import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  exchangeMissionPreview,
  fetchMissionFileContent,
  type MissionFileItem,
} from "../../../api";

function isAccessLoss(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function FilePreview({ missionId, file, onClose, onAccessLost, dedicatedPreviewEnabled = true, restoreFocusTo }: {
  missionId: string;
  file: MissionFileItem;
  onClose: () => void;
  onAccessLost?: () => void;
  dedicatedPreviewEnabled?: boolean;
  restoreFocusTo?: HTMLElement | null;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef(restoreFocusTo || (document.activeElement instanceof HTMLElement ? document.activeElement : null));
  const closeRef = useRef<HTMLButtonElement>(null);
  const dedicatedPreview = dedicatedPreviewEnabled
    && file.kind === "output"
    && file.media_type.toLowerCase() === "text/html"
    && file.state.toLowerCase() === "verified";

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setSource(null);
    setLoading(true);
    setError(null);
    const load = async () => {
      try {
        if (dedicatedPreview) {
          const session = await exchangeMissionPreview(missionId, controller.signal);
          if (!controller.signal.aborted) setSource(session.previewUrl);
        } else {
          const content = await fetchMissionFileContent(missionId, file.id, "inline");
          if (controller.signal.aborted) return;
          objectUrl = URL.createObjectURL(content);
          setSource(objectUrl);
        }
      } catch (requestError) {
        if (controller.signal.aborted) return;
        if (isAccessLoss(requestError)) {
          onAccessLost?.();
          setError("You no longer have access to this Mission.");
        } else {
          setError("This file cannot be previewed right now. Download it to continue your review.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [dedicatedPreview, file.id, missionId, onAccessLost]);

  useEffect(() => {
    closeRef.current?.focus();
    return () => opener.current?.focus();
  }, []);

  return <aside className="file-preview" role="dialog" aria-modal="true" aria-label={`${file.name} preview`} onKeyDown={(event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), [href], iframe, [tabindex]:not([tabindex='-1'])")];
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
  }}>
    <header><div><p className="workplace-eyebrow">Preview</p><h3>{file.name}</h3></div><button ref={closeRef} type="button" aria-label="Close file preview" onClick={onClose}>Close</button></header>
    {loading ? <div className="file-preview-state" role="status">Preparing a secure preview…</div> : error ? <div className="file-preview-state is-error" role="alert">{error}</div> : source ? <iframe
      src={source}
      title={`${file.name} preview`}
      tabIndex={0}
      sandbox={dedicatedPreview ? "allow-scripts allow-forms allow-same-origin" : ""}
    /> : null}
  </aside>;
}
