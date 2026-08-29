import type { MissionBootstrapRequest, StagedMissionSource } from "../../../api";

export type MissionDraftSource = {
  id: string;
  requestId: string;
  name: string;
  mediaType: string;
  size: number;
  lastModified: number;
  blob: Blob | null;
  staged: StagedMissionSource | null;
};

export type MissionDraft = {
  version: 1;
  workspaceId: string;
  humanId: string;
  outcome: string;
  bootstrapRequestId: string;
  sources: MissionDraftSource[];
  frozenRequest?: MissionBootstrapRequest;
  transactionId?: string;
  projectId?: string;
  updatedAt: number;
};

export interface MissionDraftRepository {
  load(): Promise<MissionDraft | null>;
  save(draft: MissionDraft): Promise<void>;
  discard(): Promise<void>;
}

type IdFactory = () => string;

function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function createMissionDraft(workspaceId: string, humanId: string, ids: IdFactory = randomId): MissionDraft {
  return {
    version: 1,
    workspaceId,
    humanId,
    outcome: "",
    bootstrapRequestId: ids(),
    sources: [],
    updatedAt: Date.now(),
  };
}

export function missionDraftKey(draft: Pick<MissionDraft, "workspaceId" | "humanId">): string {
  return `${draft.workspaceId}::${draft.humanId}`;
}

function sourceFingerprint(file: File): string {
  return `${file.name}\u0000${file.size}\u0000${file.type}\u0000${file.lastModified}`;
}

export function addMissionDraftFiles(draft: MissionDraft, files: File[], ids: IdFactory = randomId): MissionDraft {
  if (draft.frozenRequest || draft.transactionId) return draft;
  const known = new Set(draft.sources.map((source) => `${source.name}\u0000${source.size}\u0000${source.mediaType}\u0000${source.lastModified}`));
  const additions = files.filter((file) => !known.has(sourceFingerprint(file))).map((file) => {
    const rawId = ids();
    return {
      id: rawId,
      requestId: rawId,
      name: file.name,
      mediaType: file.type || "application/octet-stream",
      size: file.size,
      lastModified: file.lastModified,
      blob: file as Blob,
      staged: null,
    } satisfies MissionDraftSource;
  });
  return { ...draft, sources: [...draft.sources, ...additions], updatedAt: Date.now() };
}

export function removeMissionDraftSource(draft: MissionDraft, sourceId: string): MissionDraft {
  if (draft.frozenRequest || draft.transactionId) return draft;
  return { ...draft, sources: draft.sources.filter((source) => source.id !== sourceId), updatedAt: Date.now() };
}

const DATABASE = "missions-workplace";
const STORE = "mission-drafts";

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("draft_storage_failed"));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") return Promise.reject(new Error("draft_storage_unavailable"));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("draft_storage_failed"));
  });
}

async function transaction<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const database = await openDatabase();
  try {
    const tx = database.transaction(STORE, mode);
    const completed = new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error || new Error("draft_storage_failed"));
      tx.onerror = () => reject(tx.error || new Error("draft_storage_failed"));
    });
    const result = await requestResult(run(tx.objectStore(STORE)));
    await completed;
    return result;
  } finally {
    database.close();
  }
}

export function createMissionDraftRepository(workspaceId: string, humanId: string): MissionDraftRepository {
  const key = `${workspaceId}::${humanId}`;
  return {
    async load() {
      const draft = await transaction("readonly", (store) => store.get(key)) as MissionDraft | undefined;
      if (!draft || draft.workspaceId !== workspaceId || draft.humanId !== humanId || draft.version !== 1) return null;
      return draft;
    },
    async save(draft) {
      if (draft.workspaceId !== workspaceId || draft.humanId !== humanId) throw new Error("draft_identity_mismatch");
      await transaction("readwrite", (store) => store.put(draft, key));
    },
    async discard() {
      await transaction("readwrite", (store) => store.delete(key));
    },
  };
}
