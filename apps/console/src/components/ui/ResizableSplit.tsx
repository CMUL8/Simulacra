import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  left: React.ReactNode;
  right: React.ReactNode;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  hidden?: boolean;
};

export function ResizableSplit({
  left,
  right,
  defaultWidth = 420,
  minWidth = 300,
  maxWidth = 640,
  hidden = false,
}: Props) {
  const [width, setWidth] = useState(defaultWidth);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const onMove = useCallback(
    (e: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const left = containerRef.current.getBoundingClientRect().left;
      setWidth(Math.min(maxWidth, Math.max(minWidth, e.clientX - left)));
    },
    [minWidth, maxWidth],
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
    return <div className="split split-collapsed">{right}</div>;
  }

  return (
    <div className="split" ref={containerRef}>
      <div className="split-left" style={{ width }}>
        {left}
      </div>
      <div
        className="split-handle"
        role="separator"
        aria-orientation="vertical"
        onMouseDown={() => {
          dragging.current = true;
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
      />
      <div className="split-right">{right}</div>
    </div>
  );
}
