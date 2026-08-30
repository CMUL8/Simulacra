import { expect, test } from "vitest";

import documentSource from "../../index.html?raw";
import mainSource from "../main.tsx?raw";
import typography from "./typography.css?inline";

test("typography_imports_and_exposes_role_classes", () => {
  expect(typography).toContain("--mission-color-fg:");
  expect(typography).toContain("color: var(--mission-color-fg)");
  expect(typography).toContain(".mission-type-display");
  expect(typography).toContain(".mission-type-heading");
  expect(typography).toContain(".mission-type-body");
  expect(typography).toContain(".mission-type-meta");
});

test("Missions packages its fonts for private deployments", () => {
  expect(documentSource).not.toContain("fonts.googleapis.com");
  expect(documentSource).not.toContain("fonts.gstatic.com");
  expect(mainSource).toContain('@fontsource-variable/instrument-sans');
  expect(mainSource).toContain('@fontsource-variable/geist-mono');
  expect(mainSource).toContain('@fontsource/instrument-serif');
});
