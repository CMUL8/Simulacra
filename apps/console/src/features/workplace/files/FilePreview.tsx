import { lazy, Suspense, useEffect, useRef, useState } from "react";

import {
  ApiError,
  exchangeMissionPreview,
  fetchMissionFileContent,
  type MissionFileItem,
} from "../../../api";

function isAccessLoss(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

const MarkdownDocument = lazy(() => import("./MarkdownDocument"));

function normalizedMediaType(file: MissionFileItem): string {
  return file.media_type.toLowerCase().split(";", 1)[0].trim();
}

function isMarkdownFile(file: MissionFileItem): boolean {
  const mediaType = normalizedMediaType(file);
  return mediaType === "text/markdown" || mediaType === "text/x-markdown" || file.name.toLowerCase().endsWith(".md");
}

function isNetworkCapableMarkup(file: MissionFileItem): boolean {
  return ["text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"].includes(normalizedMediaType(file));
}

type PreviewResult =
  | { kind: "url"; source: string }
  | { kind: "markdown"; content: string }
  | { kind: "source"; content: string };

export function FilePreview({ missionId, file, onClose, onAccessLost, dedicatedPreviewEnabled = true, restoreFocusTo }: {
  missionId: string;
  file: MissionFileItem;
  onClose: () => void;
  onAccessLost?: () => void;
  dedicatedPreviewEnabled?: boolean;
  restoreFocusTo?: HTMLElement | null;
}) {
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef(restoreFocusTo || (document.activeElement instanceof HTMLElement ? document.activeElement : null));
  const closeRef = useRef<HTMLButtonElement>(null);
  const dedicatedPreview = dedicatedPreviewEnabled
    && file.kind === "output"
    && normalizedMediaType(file) === "text/html"
    && file.state.toLowerCase() === "verified";
  const markdownPreview = isMarkdownFile(file);
  const sourcePreview = !dedicatedPreview && isNetworkCapableMarkup(file);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setResult(null);
    setLoading(true);
    setError(null);
    const load = async () => {
      try {
        if (dedicatedPreview) {
          const session = await exchangeMissionPreview(missionId, controller.signal);
          if (!controller.signal.aborted) setResult({ kind: "url", source: session.previewUrl });
        } else {
          const content = await fetchMissionFileContent(missionId, file.id, "inline");
          if (controller.signal.aborted) return;
          if (markdownPreview) {
            setResult({ kind: "markdown", content: await content.text() });
          } else if (sourcePreview) {
            setResult({ kind: "source", content: await content.text() });
          } else {
            objectUrl = URL.createObjectURL(content);
            setResult({ kind: "url", source: objectUrl });
          }
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
  }, [dedicatedPreview, file.id, markdownPreview, missionId, onAccessLost, sourcePreview]);

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
    const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), [href], iframe, [tabindex]:not([tabindex='-1']):not([data-preview-focus-end])")];
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
    <header><div><p className="workplace-eyebrow">{markdownPreview ? "Document" : "Preview"}</p><h3>{file.name}</h3></div><button ref={closeRef} type="button" aria-label="Close file preview" onClick={onClose}>Close</button></header>
    {loading ? <div className="file-preview-state" role="status">Preparing a secure preview…</div> : error ? <div className="file-preview-state is-error" role="alert">{error}</div> : result?.kind === "markdown" ? <Suspense fallback={<div className="file-preview-state" role="status">Formatting this document…</div>}>
      <MarkdownDocument name={file.name} content={result.content} />
    </Suspense> : result?.kind === "source" ? <pre className="file-preview-source" role="document" aria-label={`${file.name} source`} tabIndex={0}><code>{result.content}</code></pre> : result?.kind === "url" ? <div className="file-preview-frame">
      <iframe
        src={result.source}
        title={`${file.name} preview`}
        tabIndex={0}
        sandbox={dedicatedPreview ? "allow-scripts allow-forms allow-same-origin" : ""}
      />
      <span className="file-preview-focus-end" data-preview-focus-end tabIndex={0} onFocus={() => closeRef.current?.focus()} />
    </div> : null}
  </aside>;
}
