import { FileCode2, FileJson2, FileSpreadsheet, FileText, type LucideIcon } from "lucide-react";

const MAP: Record<string, { icon: LucideIcon; color: string }> = {
  md: { icon: FileText, color: "#6cb6ff" },
  txt: { icon: FileText, color: "#8b949e" },
  csv: { icon: FileSpreadsheet, color: "#3fb950" },
  json: { icon: FileJson2, color: "#d2a8ff" },
  pdf: { icon: FileText, color: "#f85149" },
};

export function FileTypeIcon({ ext }: { ext: string }) {
  const cfg = MAP[ext.toLowerCase()] ?? { icon: FileCode2, color: "#8b949e" };
  const Icon = cfg.icon;
  return (
    <span className="ftype" style={{ color: cfg.color }}>
      <Icon size={14} strokeWidth={1.75} />
    </span>
  );
}
