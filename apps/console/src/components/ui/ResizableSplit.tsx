import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

type Props = {
  left: ReactNode;
  right: ReactNode;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  /** Hide the sized pane and show the other. */
  hidden?: boolean;
  /** Which pane has a fixed width. Default left (legacy). */
  sized?: "left" | "right";
};

export function ResizableSplit({
  left,
  right,
  defaultWidth = 420,
  minWidth = 300,
  maxWidth = 640,
  hidden = false,
  sized = "left",
}: Props) {
  const [width, setWidth] = useState(defaultWidth);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const onMove = useCallback(
    (e: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const next =
        sized === "right" ? rect.right - e.clientX : e.clientX - rect.left;
      setWidth(Math.min(maxWidth, Math.max(minWidth, next)));
    },
    [minWidth, maxWidth, sized],
  );

  const onUp = useCallback(() => {
    dragging.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onMove, onUp]);

  if (hidden) {
    return <div className="split split-collapsed">{sized === "right" ? left : right}</div>;
  }

  const sizedStyle = { width };
  const startDrag = () => {
    dragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  return (
    <div className={`split${sized === "right" ? " split-right-sized" : ""}`} ref={containerRef}>
      <div className={sized === "left" ? "split-left" : "split-flex"} style={sized === "left" ? sizedStyle : undefined}>
        {left}
      </div>
      <div
        className="split-handle"
        role="separator"
        aria-orientation="vertical"
        onMouseDown={startDrag}
      />
      <div className={sized === "right" ? "split-left split-right-pane" : "split-right"} style={sized === "right" ? sizedStyle : undefined}>
        {right}
      </div>
    </div>
  );
}
