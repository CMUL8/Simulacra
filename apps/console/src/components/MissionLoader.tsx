import { InlineLoader } from "generative-loaders";

type MissionLoaderVariant = "glyph" | "matrix" | "orbit" | "signal";

export function MissionLoader({
  label,
  variant = "orbit",
  compact = false,
  className = "",
}: {
  label: string;
  variant?: MissionLoaderVariant;
  compact?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`mission-loader${compact ? " mission-loader--compact" : ""}${className ? ` ${className}` : ""}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <InlineLoader
        variant={variant}
        size={compact ? "0.95em" : "1.15em"}
        color="currentColor"
      />
      <span>{label}</span>
    </span>
  );
}
