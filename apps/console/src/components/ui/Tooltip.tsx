import { type ReactNode, useId, useState } from "react";

type Props = {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom" | "right";
};

export function Tooltip({ label, children, side = "top" }: Props) {
  const [show, setShow] = useState(false);
  const id = useId();
  return (
    <span
      className="tooltip-wrap"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
    >
      <span aria-describedby={show ? id : undefined}>{children}</span>
      {show && (
        <span id={id} role="tooltip" className={`tooltip tooltip-${side}`}>
          {label}
        </span>
      )}
    </span>
  );
}
