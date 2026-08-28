import { AlertTriangle, Inbox, LockKeyhole } from "lucide-react";
import { MissionLoader } from "../../components/MissionLoader";
import type { AsyncState } from "./contracts";
import "./shared.css";

export function FeatureState({ state, title, detail, onRetry }: { state: Exclude<AsyncState, "ready">; title?: string; detail?: string; onRetry?: () => void }) {
  const Icon = state === "forbidden" ? LockKeyhole : state === "error" ? AlertTriangle : Inbox;
  const fallback = state === "loading" ? "Loading workspace" : state === "forbidden" ? "Access restricted" : state === "error" ? "Couldn’t load this view" : "Nothing here yet";
  return (
    <div className="cm-state" role={state === "error" ? "alert" : "status"} aria-live="polite" aria-busy={state === "loading"}>
      {state === "loading" ? (
        <MissionLoader label={title ?? fallback} variant="glyph" />
      ) : (
        <><Icon size={18} aria-hidden="true" /><strong>{title ?? fallback}</strong></>
      )}
      {detail ? <span>{detail}</span> : null}
      {state === "error" && onRetry ? <button type="button" onClick={onRetry}>Try again</button> : null}
    </div>
  );
}
