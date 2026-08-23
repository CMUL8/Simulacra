import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCmul8Observability, fetchCmul8ObservabilityDetail } from "../../api";
import { ObservabilityConsole } from "./ObservabilityConsole";
import type { EntityDetail, ObservabilityKind, ObservabilitySnapshot, ObservabilityState } from "./contracts";

export function ObservabilityContainer({ projectId }: { projectId: string }) {
  const [snapshot, setSnapshot] = useState<ObservabilitySnapshot>();
  const [state, setState] = useState<ObservabilityState>("loading");
  const [error, setError] = useState<string>();

  const refresh = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const value = await fetchCmul8Observability<ObservabilitySnapshot>(projectId);
      setSnapshot(value);
      setState(value.overview.runs || value.actions.length ? "ready" : "empty");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Telemetry unavailable";
      setError(message);
      setState(message.includes("403") ? "forbidden" : "error");
    }
  }, [projectId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const adapter = useMemo(() => ({
    refresh,
    loadDetail: (kind: ObservabilityKind, id: string) =>
      fetchCmul8ObservabilityDetail<EntityDetail>(projectId, kind, id),
    openDeepLink: (link: string) => {
      const url = new URL(window.location.href);
      const params = new URLSearchParams(link.startsWith("?") ? link.slice(1) : link);
      for (const key of ["obsView", "obsKind", "obsId", "trace"]) url.searchParams.delete(key);
      params.forEach((value, key) => url.searchParams.set(key, value));
      window.history.replaceState(null, "", url);
    },
  }), [projectId, refresh]);

  return <ObservabilityConsole snapshot={snapshot} state={state} errorMessage={error} initialDeepLink={window.location.search} adapter={adapter} />;
}
