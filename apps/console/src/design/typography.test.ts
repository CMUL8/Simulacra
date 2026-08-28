import { expect, test } from "vitest";

import typography from "./typography.css?inline";

test("typography_imports_and_exposes_role_classes", () => {
  expect(typography).toContain("--mission-color-fg: #f5f7ff");
  expect(typography).toContain(".mission-type-display");
  expect(typography).toContain(".mission-type-heading");
  expect(typography).toContain(".mission-type-body");
  expect(typography).toContain(".mission-type-meta");
});
