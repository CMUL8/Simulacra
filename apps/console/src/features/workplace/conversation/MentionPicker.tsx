import { useEffect, useMemo, useState } from "react";

export type MentionChoice = {
  id: string;
  name: string;
  detail: string;
  kind: "agent" | "human" | "crew";
};

export function MentionPicker({ choices, query, active, onChoose, onClose }: {
  choices: MentionChoice[];
  query: string;
  active: boolean;
  onChoose: (choice: MentionChoice) => void;
  onClose: () => void;
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return choices.filter((choice) => !needle || `${choice.name} ${choice.detail}`.toLocaleLowerCase().includes(needle));
  }, [choices, query]);

  useEffect(() => setActiveIndex(0), [query]);

  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((index) => visible.length ? (index + 1) % visible.length : 0);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => visible.length ? (index - 1 + visible.length) % visible.length : 0);
      } else if (event.key === "Enter" && visible[activeIndex]) {
        event.preventDefault();
        onChoose(visible[activeIndex]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, activeIndex, onChoose, onClose, visible]);

  if (!active) return null;
  return <div className="mention-picker" role="listbox" id="mission-mention-picker" aria-label="Mission crew">
    {visible.length ? visible.map((choice, index) => <button
      key={`${choice.kind}:${choice.id}`}
      className={index === activeIndex ? "is-active" : ""}
      type="button"
      role="option"
      aria-selected={index === activeIndex}
      onMouseEnter={() => setActiveIndex(index)}
      onClick={() => onChoose(choice)}
    >
      <span aria-hidden="true">@</span>
      <strong>{choice.name}</strong>
      <small>{choice.detail}</small>
    </button>) : <p>No crew member matches.</p>}
  </div>;
}
