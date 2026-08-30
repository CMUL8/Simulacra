import { Bell, BellOff } from "lucide-react";
import { useEffect, useState } from "react";

import type { NotificationPreference } from "../../../api";
import "./onboarding.css";

type SaveBody = {
  expected_revision: number;
  event_selection: string;
  channels: string[];
  digest: string;
  muted_mission_ids: string[];
};

type Props = {
  preference: NotificationPreference;
  missions: Array<{ id: string; title: string }>;
  actionableCount?: number;
  onSave: (body: SaveBody) => Promise<void>;
};

export function NotificationPreferences({ preference, missions, actionableCount = 0, onSave }: Props) {
  const [selection, setSelection] = useState(preference.event_selection);
  const [channels, setChannels] = useState(preference.channels);
  const [digest, setDigest] = useState(preference.digest);
  const [muted, setMuted] = useState(preference.muted_mission_ids);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => {
    setSelection(preference.event_selection);
    setChannels(preference.channels);
    setDigest(preference.digest);
    setMuted(preference.muted_mission_ids);
  }, [preference]);

  const toggle = (collection: string[], value: string) => collection.includes(value)
    ? collection.filter((item) => item !== value)
    : [...collection, value];

  async function save() {
    setState("saving");
    try {
      await onSave({
        expected_revision: preference.revision,
        event_selection: selection,
        channels,
        digest,
        muted_mission_ids: muted,
      });
      setState("saved");
    } catch {
      setState("error");
    }
  }

  return (
    <section className="notification-preferences" aria-labelledby="notification-preferences-title">
      <header>
        <Bell size={16} aria-hidden />
        <div><h2 id="notification-preferences-title">Notifications</h2><p>Choose when Missions should bring you back.</p></div>
      </header>
      <div className="notification-preferences__grid">
        <label>Notify me about
          <select value={selection} onChange={(event) => setSelection(event.target.value)}>
            <option value="all_actionable">Everything that needs me</option>
            <option value="mentions_and_decisions">Mentions and decisions</option>
            <option value="off">No external notifications</option>
          </select>
        </label>
        <label>Digest
          <select value={digest} onChange={(event) => setDigest(event.target.value)}>
            <option value="off">No digest</option>
            <option value="immediate">Immediate</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>
      </div>
      <fieldset>
        <legend>Channels</legend>
        {["browser", "email", "push"].map((channel) => (
          <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={() => setChannels(toggle(channels, channel))} />{channel[0].toUpperCase() + channel.slice(1)}</label>
        ))}
      </fieldset>
      {missions.length ? <fieldset>
        <legend>Quiet Missions</legend>
        {missions.map((mission) => <label key={mission.id}>
          <input type="checkbox" checked={muted.includes(mission.id)} onChange={() => setMuted(toggle(muted, mission.id))} />
          Mute external notifications for {mission.title}
        </label>)}
      </fieldset> : null}
      <aside className="notification-preferences__attention">
        <BellOff size={16} aria-hidden />
        <span>{actionableCount} item{actionableCount === 1 ? "" : "s"} still need you in Missions. <strong>Needs you remains available</strong> even when external notifications are muted.</span>
      </aside>
      <footer>
        <button type="button" onClick={() => void save()} disabled={state === "saving"}>{state === "saving" ? "Saving…" : "Save notification preferences"}</button>
        {state === "saved" ? <span role="status">Preferences saved.</span> : null}
        {state === "error" ? <span role="alert">Preferences could not be saved. Try again.</span> : null}
      </footer>
    </section>
  );
}
