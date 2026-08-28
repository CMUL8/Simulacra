import { expect, test } from "vitest";

import motion from "./motion.css?inline";

test("motion_respects_reduced_motion", () => {
  expect(motion).toContain("--mission-motion-standard: 220ms");
  expect(motion).toContain("@media (prefers-reduced-motion: reduce)");
  expect(motion).toContain("animation-duration: 1ms !important");
  expect(motion).toContain("transition-duration: 1ms !important");
});
