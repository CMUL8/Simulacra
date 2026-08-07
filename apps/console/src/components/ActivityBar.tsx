import { AppWindow, FolderOpen, MessageSquare, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Tooltip } from "./ui/Tooltip";

export type Panel = "chat" | "data" | "app";

type Props = {
  active: Panel;
  onChange: (p: Panel) => void;
};

const items: { id: Panel; label: string; icon: ReactNode }[] = [
  { id: "chat", label: "Agent chat", icon: <MessageSquare size={20} strokeWidth={1.75} /> },
  { id: "data", label: "Data room", icon: <FolderOpen size={20} strokeWidth={1.75} /> },
  { id: "app", label: "App preview", icon: <AppWindow size={20} strokeWidth={1.75} /> },
];

export function ActivityBar({ active, onChange }: Props) {
  return (
    <nav className="activity-bar" aria-label="Primary navigation">
      <Tooltip label="Simulacra">
        <div className="activity-brand">
          <Sparkles size={16} strokeWidth={2} />
        </div>
      </Tooltip>
      <div className="activity-nav">
        {items.map((item) => (
          <Tooltip key={item.id} label={item.label} side="right">
            <button
              type="button"
              className={`activity-btn ${active === item.id ? "active" : ""}`}
              aria-current={active === item.id ? "page" : undefined}
              onClick={() => onChange(item.id)}
            >
              {item.icon}
            </button>
          </Tooltip>
        ))}
      </div>
    </nav>
  );
}
