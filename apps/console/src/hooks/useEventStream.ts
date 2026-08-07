import { useCallback, useEffect, useRef, useState } from "react";
import { listEvents, type AgentEvent, subscribeEvents } from "../api";

export function useEventStream(projectId: string | null) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const seen = useRef(new Set<string>());

  const push = useCallback((evt: AgentEvent) => {
    if (seen.current.has(evt.id)) return;
    seen.current.add(evt.id);
    setEvents((prev) => [...prev, evt]);
  }, []);

  useEffect(() => {
    if (!projectId) {
      setEvents([]);
      seen.current.clear();
      return;
    }

    seen.current.clear();
    listEvents(projectId).then((hist) => {
      setEvents(hist);
      hist.forEach((e) => seen.current.add(e.id));
    });

    const unsub = subscribeEvents(projectId, push);
    return unsub;
  }, [projectId, push]);

  const reset = useCallback(() => {
    seen.current.clear();
    setEvents([]);
  }, []);

  return { events, reset };
}
