import type { ArtifactKind } from "../api";

const FORMAT_LABEL: Record<ArtifactKind, string> = {
  data_app: "app",
  report: "report",
  slides: "slides",
  one_pager: "one-pager",
};

function snippet(prompt: string, max = 120): string {
  const t = prompt.trim().replace(/\s+/g, " ");
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1).trimEnd()}…`;
}

type Props = {
  prompt: string;
  artifactKind: ArtifactKind;
  clerkEnabled?: boolean;
  onCreateAccount: () => void;
  onSignIn: () => void;
  onEdit: () => void;
};

export function GuestAuthGate({
  prompt,
  artifactKind,
  clerkEnabled = false,
  onCreateAccount,
  onSignIn,
  onEdit,
}: Props) {
  const label = FORMAT_LABEL[artifactKind] || "app";
  const quote = snippet(prompt);

  return (
    <div className="guest-gate" role="region" aria-label="Continue with an account">
      <p className="guest-gate-ack">
        That {label} is ready to start from what you described.
      </p>
      <p className="guest-gate-quote">“{quote}”</p>
      <p className="guest-gate-invite">
        {clerkEnabled
          ? "Create an account to continue — Google sign-in is available in the next step."
          : "Create an account to continue, or sign in if you already have one."}
      </p>
      <div className="guest-gate-actions">
        <button type="button" className="guest-gate-primary" onClick={onCreateAccount}>
          Create account
        </button>
        <button type="button" className="guest-gate-secondary" onClick={onSignIn}>
          Sign in
        </button>
      </div>
      <button type="button" className="guest-gate-edit" onClick={onEdit}>
        Edit prompt
      </button>
    </div>
  );
}

export { FORMAT_LABEL as GUEST_FORMAT_LABEL, snippet as guestPromptSnippet };
