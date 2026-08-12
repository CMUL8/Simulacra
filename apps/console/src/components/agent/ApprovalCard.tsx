/** Approval card — human-in-the-loop before acting (Beautiful UI 04). */
type Option = { id: string; label: string; primary?: boolean };

type Props = {
  question: string;
  options: Option[];
  busy?: boolean;
  onChoose: (id: string) => void;
  onDismiss?: () => void;
};

export function ApprovalCard({ question, options, busy, onChoose, onDismiss }: Props) {
  return (
    <div className="bui-approval" role="group" aria-label="Approval needed">
      <p className="bui-approval-q">{question}</p>
      <div className="bui-approval-opts">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            className={`bui-approval-opt${o.primary ? " primary" : ""}`}
            disabled={busy}
            onClick={() => onChoose(o.id)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {onDismiss ? (
        <button type="button" className="bui-approval-dismiss" onClick={onDismiss} disabled={busy}>
          Not now
        </button>
      ) : null}
    </div>
  );
}
