import { useId } from "react";

import { MissionLoader } from "../../../components/MissionLoader";
import type { MissionDraftRepository } from "./missionDraftStore";
import { type MissionBootstrapClients, useMissionBootstrap } from "./useMissionBootstrap";
import "./new-mission.css";

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function MissionCreationFlow({
  workspaceId,
  humanId,
  repository,
  clients,
  onComplete,
  onCancel,
}: {
  workspaceId: string;
  humanId: string;
  repository?: MissionDraftRepository;
  clients?: MissionBootstrapClients;
  onComplete: (missionId: string) => void;
  onCancel: () => void;
}) {
  const sourceInputId = useId();
  const bootstrap = useMissionBootstrap({ workspaceId, humanId, repository, clients, onComplete });
  const outcome = bootstrap.draft?.outcome || "";
  const sources = bootstrap.draft?.sources || [];
  const locked = bootstrap.working || bootstrap.blocked || Boolean(bootstrap.draft?.frozenRequest);
  const progress = bootstrap.phase === "sources"
    ? "Adding your sources…"
    : bootstrap.phase === "provisioning"
      ? "Preparing your Mission…"
      : "Opening your Mission…";

  return <section className="mission-create" aria-labelledby="mission-create-title">
    <header className="mission-create-heading">
      <button className="mission-create-back" type="button" onClick={onCancel}>Back to Missions</button>
      <p className="mission-create-kicker">New Mission</p>
      <h1 id="mission-create-title">Create a Mission</h1>
      <p>Set the outcome. Agents carry it through; humans guide the decisions, verify the evidence, and approve what ships.</p>
    </header>

    <form className="mission-create-form" onSubmit={(event) => {
      event.preventDefault();
      void bootstrap.create(outcome);
    }}>
      <label className="mission-create-field" htmlFor="mission-outcome">
        <span>What should this Mission accomplish?</span>
        <textarea
          id="mission-outcome"
          aria-label="Mission outcome"
          rows={5}
          autoFocus
          disabled={!bootstrap.ready || locked}
          value={outcome}
          placeholder="For example: reconcile this month’s invoices, flag every exception, and prepare a review pack with source evidence."
          onChange={(event) => void bootstrap.setOutcome(event.currentTarget.value)}
        />
      </label>

      <div className="mission-create-sources">
        <div>
          <strong>Sources</strong>
          <span>Optional · documents, spreadsheets, images, or other working files</span>
        </div>
        <label className={`mission-source-picker${locked ? " is-disabled" : ""}`} htmlFor={sourceInputId}>
          Add source files
          <input
            id={sourceInputId}
            aria-label="Add source files"
            type="file"
            multiple
            disabled={!bootstrap.ready || locked}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files || []);
              if (files.length) void bootstrap.setFiles(files);
              event.currentTarget.value = "";
            }}
          />
        </label>
      </div>

      {sources.length ? <ul className="mission-source-list" aria-label="Selected source files">
        {sources.map((source) => <li key={source.id}>
          <span><strong>{source.name}</strong><small>{fileSize(source.size)}</small></span>
          {source.staged ? <span className="mission-source-ready">Added</span> : <button
            type="button"
            aria-label={`Remove ${source.name}`}
            disabled={locked}
            onClick={() => void bootstrap.removeFile(source.id)}
          >Remove</button>}
        </li>)}
      </ul> : null}

      {bootstrap.error ? <div className={`mission-create-message${bootstrap.blocked ? " is-blocked" : ""}`} role="alert">
        <strong>{bootstrap.blocked ? "This draft cannot continue" : "Creation paused"}</strong>
        <span>{bootstrap.error}</span>
      </div> : null}

      {bootstrap.working ? <div className="mission-create-progress">
        <MissionLoader label={progress} variant="signal" />
        <span>You can safely leave this page. Your Mission will keep preparing.</span>
      </div> : null}

      <footer className="mission-create-actions">
        {bootstrap.blocked ? <button className="mission-create-secondary" type="button" onClick={() => void bootstrap.discard()}>Start a new Mission</button> : null}
        {bootstrap.canRetry ? <button className="mission-create-secondary" type="button" onClick={() => void bootstrap.retry()}>Retry</button> : null}
        {!bootstrap.blocked ? <button
          className="mission-create-primary"
          type="submit"
          disabled={!bootstrap.ready || bootstrap.working || outcome.trim().length < 3}
        >{bootstrap.working ? "Creating…" : "Create Mission"}</button> : null}
      </footer>
    </form>
  </section>;
}
