import { AlertTriangle, RefreshCw, Radio, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { deriveWorkState, formatElapsed, type WorkEvent } from "./contracts";
import "./shared.css";

export function WorkStatus({ events, connected = true, stallAfterMs, onReconnect, onRetry }: { events: WorkEvent[]; connected?: boolean; stallAfterMs?: number; onReconnect?: () => void; onRetry?: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const work = useMemo(() => deriveWorkState(events, now, stallAfterMs), [events, now, stallAfterMs]);
  if (work.state === "idle") return null;
  const interrupted = !connected || work.state === "stalled" || work.state === "failed";
  return (
    <section className={`cm-work cm-work--${work.state}`} aria-label="Current work" aria-live="polite" aria-busy={work.state === "running"}>
      <div className="cm-work__signal">{interrupted ? <AlertTriangle size={15} /> : <Radio size={15} />}<span>{!connected ? "Connection lost" : work.state === "stalled" ? "Heartbeat overdue" : work.state}</span></div>
      <div className="cm-work__body"><strong>{work.phase ?? "Work in progress"}</strong><span>{work.specialist ?? "Coordinating"}</span></div>
      <time className="cm-work__time">{formatElapsed(work.elapsedSeconds)}</time>
      {work.lastMessage ? <p>{work.lastMessage}</p> : null}
      {interrupted ? <div className="cm-work__actions">
        {!connected && onReconnect ? <button type="button" onClick={onReconnect}><RefreshCw size={13} /> Reconnect</button> : null}
        {(work.state === "stalled" || work.state === "failed") && onRetry ? <button type="button" onClick={onRetry}><RotateCcw size={13} /> Retry phase</button> : null}
      </div> : null}
    </section>
  );
}
