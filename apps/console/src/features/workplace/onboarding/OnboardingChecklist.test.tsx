import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { OnboardingChecklist } from "./OnboardingChecklist";

afterEach(cleanup);

test("onboarding_checklist_has_no_runtime_copy", () => {
  const onAddSource = vi.fn();
  render(
    <OnboardingChecklist
      readiness={{ sourceAdded: false, crewReady: true, workPlanApproved: false }}
      onAddSource={onAddSource}
      onOpenCrew={vi.fn()}
      onReviewPlan={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "Ready your Mission" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add a source" })).toBeInTheDocument();
  expect(screen.getByText("Crew ready")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve how the crew will work" })).toBeInTheDocument();
  expect(screen.queryByText(/runtime|provider|model|computer|host|MCP/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add a source" }));
  expect(onAddSource).toHaveBeenCalledTimes(1);
});

test("completed_onboarding_disappears_instead_of_becoming_another_tab", () => {
  const { container } = render(
    <OnboardingChecklist
      readiness={{ sourceAdded: true, crewReady: true, workPlanApproved: true }}
      onAddSource={vi.fn()}
      onOpenCrew={vi.fn()}
      onReviewPlan={vi.fn()}
    />,
  );

  expect(container).toBeEmptyDOMElement();
});
