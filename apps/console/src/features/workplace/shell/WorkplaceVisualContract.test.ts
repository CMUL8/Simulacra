import { expect, test } from "vitest";

import attentionStyles from "../attention/attention.css?raw";
import conversationStyles from "../conversation/conversation.css?raw";
import crewStyles from "../crew/crew-actions.css?raw";
import fileStyles from "../files/files.css?raw";
import workStyles from "../work/work.css?raw";
import workplaceStyles from "./workplace.css?raw";
import tokens from "../../../design/tokens.css?raw";

test("the workplace uses one premium interaction and surface contract", () => {
  for (const token of [
    "--mission-color-surface-subtle",
    "--mission-color-surface-hover",
    "--mission-color-accent-soft",
    "--mission-color-warning-soft",
    "--mission-color-success-soft",
    "--mission-color-danger-soft",
    "--mission-control-compact",
    "--mission-drawer-width",
    "--mission-shadow-drawer",
  ]) expect(tokens).toContain(token);

  const productStyles = [workplaceStyles, conversationStyles, crewStyles, workStyles, fileStyles, attentionStyles];
  productStyles.forEach((styles) => {
    expect(styles).not.toMatch(/gradient\s*\(/i);
    expect(styles).not.toMatch(/#[0-9a-f]{3,8}|rgb\(/i);
  });
  expect(workplaceStyles).toMatch(/\.workplace-nav-target\.is-utility\s*\{[^}]*margin-block-start:\s*auto/s);
  expect(workplaceStyles).toMatch(/\.mission-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(min\(24rem,\s*100%\),\s*1fr\)\)/s);
  expect(workplaceStyles).toMatch(/\.mission-summary-card\s*\{[^}]*transition:[^}]*box-shadow/s);
  expect(attentionStyles).toMatch(/\.attention-row:not\(\.is-read\)::before\s*\{[^}]*background:\s*var\(--mission-color-warning\)/s);
});

test("drawers and dialogs share calm surfaces and compact controls", () => {
  expect(conversationStyles).toMatch(/\.thread-drawer\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)/s);
  expect(workStyles).toMatch(/\.work-detail\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)[^}]*width:\s*min\(var\(--mission-drawer-width\),\s*100%\)/s);
  expect(fileStyles).toMatch(/\.file-detail,\s*\.file-preview\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)/s);
  expect(crewStyles).toMatch(/\.crew-dialog\s*\{[^}]*box-shadow:\s*var\(--mission-shadow-dialog\)/s);
  expect(workStyles).toMatch(/min-height:\s*var\(--mission-control-compact\)/s);
  expect(fileStyles).toMatch(/min-height:\s*var\(--mission-control-compact\)/s);
});
