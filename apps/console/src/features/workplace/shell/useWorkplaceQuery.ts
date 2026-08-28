import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  listMissionSummaries,
  listWorkspaceAttention,
  openWorkspaceEventStream,
  type AttentionItem,
  type WorkspaceEventStream,
} from "../../../api";
import type {
  AttentionFilter,
  MissionStateFilter,
  WorkplaceAttentionPage,
  WorkplaceMissionPage,
} from "./contracts";

type QueryState<T> = {
  data: T | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  retry: () => void;
  loadMore: () => void;
};

function merge<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const replacements = new Map(incoming.map((item) => [item.id, item]));
  const merged = current.map((item) => replacements.get(item.id) ?? item);
  const existing = new Set(current.map((item) => item.id));
  return [...merged, ...incoming.filter((item) => !existing.has(item.id))];
}

export function useMissionSummaries(state: MissionStateFilter, enabled: boolean): QueryState<WorkplaceMissionPage> {
  const request = useRef(0);
  const moreInFlight = useRef(false);
  const [result, setResult] = useState<Omit<QueryState<WorkplaceMissionPage>, "retry" | "loadMore">>({
    data: null,
    loading: enabled,
    loadingMore: false,
    error: null,
  });
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const current = ++request.current;
    moreInFlight.current = false;
    setResult({ data: null, loading: true, loadingMore: false, error: null });
    void listMissionSummaries(state)
      .then((data) => {
        if (request.current === current) setResult({ data, loading: false, loadingMore: false, error: null });
      })
      .catch(() => {
        if (request.current === current) setResult({ data: null, loading: false, loadingMore: false, error: "Missions could not be loaded." });
      });
    return () => {
      request.current += 1;
      moreInFlight.current = false;
    };
  }, [enabled, state, retryNonce]);

  const loadMore = useCallback(() => {
    const cursor = result.data?.next_cursor;
    if (!cursor || moreInFlight.current) return;
    const current = request.current;
    moreInFlight.current = true;
    setResult((previous) => ({ ...previous, loadingMore: true, error: null }));
    void listMissionSummaries(state, cursor)
      .then((page) => {
        if (request.current !== current) return;
        setResult((previous) => previous.data
          ? {
              data: { ...page, items: merge(previous.data.items, page.items) },
              loading: false,
              loadingMore: false,
              error: null,
            }
          : { data: page, loading: false, loadingMore: false, error: null });
      })
      .catch(() => {
        if (request.current === current) {
          setResult((previous) => ({ ...previous, loadingMore: false, error: "More Missions could not be loaded." }));
        }
      })
      .finally(() => {
        if (request.current === current) moreInFlight.current = false;
      });
  }, [result.data?.next_cursor, state]);

  const retry = useCallback(() => {
    if (result.data && result.error) loadMore();
    else setRetryNonce((value) => value + 1);
  }, [loadMore, result.data, result.error]);

  return { ...result, retry, loadMore };
}

export function useAttention(filter: AttentionFilter, enabled: boolean): QueryState<WorkplaceAttentionPage> & {
  updateItem: (item: AttentionItem) => void;
} {
  const request = useRef(0);
  const moreInFlight = useRef(false);
  const [result, setResult] = useState<Omit<QueryState<WorkplaceAttentionPage>, "retry" | "loadMore">>({
    data: null,
    loading: enabled,
    loadingMore: false,
    error: null,
  });
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const current = ++request.current;
    moreInFlight.current = false;
    setResult({ data: null, loading: true, loadingMore: false, error: null });
    void listWorkspaceAttention(filter)
      .then((data) => {
        if (request.current === current) setResult({ data, loading: false, loadingMore: false, error: null });
      })
      .catch(() => {
        if (request.current === current) setResult({ data: null, loading: false, loadingMore: false, error: "Needs you could not be loaded." });
      });
    return () => {
      request.current += 1;
      moreInFlight.current = false;
    };
  }, [enabled, filter, retryNonce]);

  const loadMore = useCallback(() => {
    const cursor = result.data?.next_cursor;
    if (!cursor || moreInFlight.current) return;
    const current = request.current;
    moreInFlight.current = true;
    setResult((previous) => ({ ...previous, loadingMore: true, error: null }));
    void listWorkspaceAttention(filter, cursor)
      .then((page) => {
        if (request.current !== current) return;
        setResult((previous) => previous.data
          ? {
              data: {
                ...page,
                items: merge(previous.data.items, page.items),
              },
              loading: false,
              loadingMore: false,
              error: null,
            }
          : { data: page, loading: false, loadingMore: false, error: null });
      })
      .catch(() => {
        if (request.current === current) {
          setResult((previous) => ({ ...previous, loadingMore: false, error: "More attention could not be loaded." }));
        }
      })
      .finally(() => {
        if (request.current === current) moreInFlight.current = false;
      });
  }, [filter, result.data?.next_cursor]);

  const retry = useCallback(() => {
    if (result.data && result.error) loadMore();
    else setRetryNonce((value) => value + 1);
  }, [loadMore, result.data, result.error]);

  const updateItem = useCallback((item: AttentionItem) => {
    setResult((previous) => {
      if (!previous.data) return previous;
      const existing = previous.data.items.find((candidate) => candidate.id === item.id);
      const unreadDelta = existing && !existing.read && item.read ? -1 : existing?.read && !item.read ? 1 : 0;
      return {
        ...previous,
        data: {
          ...previous.data,
          items: previous.data.items.map((candidate) => candidate.id === item.id ? item : candidate),
          unread_count: Math.max(0, previous.data.unread_count + unreadDelta),
        },
      };
    });
  }, []);

  return { ...result, retry, loadMore, updateItem };
}

export function useMissionConversationLive({
  enabled,
  missionId,
  onRefresh,
  onAccessLost,
  stream = openWorkspaceEventStream,
  pollIntervalMs = 15_000,
}: {
  enabled: boolean;
  missionId: string;
  onRefresh: () => Promise<void>;
  onAccessLost?: () => void;
  stream?: WorkspaceEventStream;
  pollIntervalMs?: number;
}): void {
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;
  const accessLostRef = useRef(onAccessLost);
  accessLostRef.current = onAccessLost;

  useEffect(() => {
    if (!enabled || !missionId) return;
    const controller = new AbortController();
    const seenEventIds = new Set<string>();
    const seenEventOrder: string[] = [];
    let lastEventId: string | null = null;
    let failures = 0;
    let reconnectTimer: number | null = null;
    let pollTimer: number | null = null;
    let healthyTimer: number | null = null;
    let refreshing = false;
    let refreshQueued = false;
    let accessLost = false;

    const clearReconnect = () => {
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };
    const stopPolling = () => {
      if (pollTimer !== null) window.clearInterval(pollTimer);
      pollTimer = null;
    };
    const clearHealthyTimer = () => {
      if (healthyTimer !== null) window.clearTimeout(healthyTimer);
      healthyTimer = null;
    };
    const loseAccess = () => {
      if (accessLost || controller.signal.aborted) return;
      accessLost = true;
      clearReconnect();
      stopPolling();
      clearHealthyTimer();
      accessLostRef.current?.();
      controller.abort();
    };
    const refreshDurablePage = async () => {
      if (refreshing) {
        refreshQueued = true;
        return;
      }
      refreshing = true;
      try {
        do {
          refreshQueued = false;
          try {
            await refreshRef.current();
          } catch (error) {
            if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
              loseAccess();
              return;
            }
            /* The next wake-up or bounded poll repairs a transient read failure. */
          }
        } while (refreshQueued && !controller.signal.aborted);
      } finally {
        refreshing = false;
      }
    };
    const startPolling = () => {
      if (pollTimer !== null || controller.signal.aborted) return;
      pollTimer = window.setInterval(() => void refreshDurablePage(), Math.max(1_000, pollIntervalMs));
    };

    const connect = async () => {
      if (controller.signal.aborted) return;
      clearReconnect();
      try {
        await stream({
          lastEventId,
          signal: controller.signal,
          onOpen: () => {
            clearHealthyTimer();
            healthyTimer = window.setTimeout(() => {
              failures = 0;
              stopPolling();
              healthyTimer = null;
            }, 20_000);
          },
          onWakeUp: (event) => {
            if (seenEventIds.has(event.id)) return;
            seenEventIds.add(event.id);
            seenEventOrder.push(event.id);
            if (seenEventOrder.length > 512) {
              const expired = seenEventOrder.shift();
              if (expired) seenEventIds.delete(expired);
            }
            lastEventId = event.id;
            failures = 0;
            clearHealthyTimer();
            stopPolling();
            if (event.mission_id === missionId || event.type === "workspace.reset" || event.type === "workspace_reset") {
              void refreshDurablePage();
            }
          },
        });
        if (controller.signal.aborted) return;
        throw new Error("stream ended");
      } catch (error) {
        clearHealthyTimer();
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          loseAccess();
          return;
        }
        failures += 1;
        if (failures >= 2) startPolling();
        const delay = Math.min(10_000, 1_000 * failures);
        reconnectTimer = window.setTimeout(() => void connect(), delay);
      }
    };

    void connect();
    return () => {
      controller.abort();
      clearReconnect();
      stopPolling();
      clearHealthyTimer();
    };
  }, [enabled, missionId, pollIntervalMs, stream]);
}
