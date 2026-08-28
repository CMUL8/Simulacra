import { Check, Circle, FilePlus2, ShieldCheck, UsersRound } from "lucide-react";

import "./onboarding.css";

export type MissionReadiness = {
  sourceAdded: boolean;
  crewReady: boolean;
  workPlanApproved: boolean;
};

type Props = {
  readiness: MissionReadiness;
  onAddSource: () => void;
  onOpenCrew: () => void;
  onReviewPlan: () => void;
};

export function OnboardingChecklist({ readiness, onAddSource, onOpenCrew, onReviewPlan }: Props) {
  const steps = [
    { complete: readiness.sourceAdded, label: readiness.sourceAdded ? "Source added" : "Add a source", detail: "Give the crew the documents or data it needs.", action: onAddSource, Icon: FilePlus2 },
    { complete: readiness.crewReady, label: readiness.crewReady ? "Crew ready" : "Shape the crew", detail: "Add agents and invite the humans who will guide the work.", action: onOpenCrew, Icon: UsersRound },
    { complete: readiness.workPlanApproved, label: readiness.workPlanApproved ? "Working agreement approved" : "Approve how the crew will work", detail: "Confirm responsibilities, access, and human checkpoints.", action: onReviewPlan, Icon: ShieldCheck },
  ];
  if (steps.every((step) => step.complete)) return null;

  return (
    <section className="mission-onboarding" aria-labelledby="mission-onboarding-title">
      <header>
        <span>FIRST MISSION</span>
        <h2 id="mission-onboarding-title">Ready your Mission</h2>
        <p>Complete the remaining setup in the Mission itself.</p>
      </header>
      <ol>
        {steps.map(({ complete, label, detail, action, Icon }) => (
          <li key={label} className={complete ? "is-complete" : undefined}>
            <span className="mission-onboarding__state" aria-hidden>{complete ? <Check size={15} /> : <Circle size={15} />}</span>
            <Icon className="mission-onboarding__icon" size={17} aria-hidden />
            <span><strong>{label}</strong><small>{detail}</small></span>
            {!complete ? <button type="button" onClick={action}>{label}</button> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
