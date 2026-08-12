import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

const STEPS = [
  "Reading data room",
  "Extracting structured rows",
  "Running eval gates",
  "Generating React app",
  "Starting preview",
];

type Props = { active: boolean };

export function BuildSteps({ active }: Props) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!active) {
      setStep(0);
      return;
    }
    const id = setInterval(() => {
      setStep((s) => (s < STEPS.length - 1 ? s + 1 : s));
    }, 2200);
    return () => clearInterval(id);
  }, [active]);

  if (!active) return null;

  return (
    <div className="build-steps">
      {STEPS.map((label, i) => {
        const done = i < step;
        const current = i === step;
        return (
          <div key={label} className={`build-step ${done ? "done" : ""} ${current ? "current" : ""}`}>
            {done ? (
              <CheckCircle2 size={14} className="step-icon done" />
            ) : current ? (
              <Loader2 size={14} className="step-icon spin" />
            ) : (
              <Circle size={14} className="step-icon" />
            )}
            <span>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
