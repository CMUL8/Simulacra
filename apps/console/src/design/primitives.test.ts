import { renderWithFixture } from "../test/fixtures/workplace";
import { createElement } from "react";
import { expect, test } from "vitest";

import primitives from "./primitives.css?inline";

test("primitives_render_focus_and_state_contracts", () => {
  const style = document.createElement("style");
  style.textContent = primitives;
  document.head.append(style);

  const { getByRole, unmount } = renderWithFixture(
    createElement("button", { className: "mission-button", "data-state": "ready" }, "Continue"),
  );
  const button = getByRole("button", { name: "Continue" });
  button.focus();

  expect(button).toHaveFocus();
  expect(primitives).toContain(".mission-button:focus-visible");
  expect(primitives).toContain('[data-state="ready"]');
  expect(primitives).toContain('[aria-invalid="true"]');

  unmount();
  style.remove();
});
