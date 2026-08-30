import { expect, test } from "vitest";

import attentionStyles from "../attention/attention.css?raw";
import conversationStyles from "../conversation/conversation.css?raw";
import crewStyles from "../crew/crew-actions.css?raw";
import fileStyles from "../files/files.css?raw";
import workStyles from "../work/work.css?raw";
import workplaceStyles from "./workplace.css?raw";
import tokens from "../../../design/tokens.css?raw";
import typography from "../../../design/typography.css?raw";
import workplaceShellSource from "./WorkplaceShell.tsx?raw";
import missionRoomSource from "../conversation/MissionConversationWorkspace.tsx?raw";

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
    "--mission-type-title-size",
    "--mission-type-body-size",
    "--mission-type-label-size",
    "--mission-type-meta-size",
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

test("the workplace uses one deliberate type and icon system", () => {
  for (const token of [
    "--mission-font-ui",
    "--mission-font-mono",
    "--mission-font-display",
    "--mission-font-weight-regular",
    "--mission-font-weight-medium",
    "--mission-font-weight-semibold",
    "--mission-leading-body",
    "--mission-tracking-heading",
    "--mission-icon-stroke",
  ]) expect(tokens).toContain(token);
  expect(typography).toContain("font-family: var(--mission-font-ui)");
  expect(typography).not.toContain("font-family: Inter");
  expect(workplaceStyles).toMatch(/\.workplace-shell\s*\{[^}]*font-family:\s*var\(--mission-font-ui\)/s);
  expect(workplaceStyles).toMatch(/\.workplace-shell\s+svg\s*\{[^}]*stroke-width:\s*var\(--mission-icon-stroke\)/s);
  expect(workplaceStyles).toContain("font-variant-numeric: tabular-nums");
  expect(workplaceShellSource).not.toContain('<p className="workplace-eyebrow">Workspace</p>');
  expect(missionRoomSource).not.toContain('<p className="workplace-eyebrow">Mission</p>');
});

test("drawers and dialogs share calm surfaces and compact controls", () => {
  expect(conversationStyles).toMatch(/\.thread-drawer\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)/s);
  expect(workStyles).toMatch(/\.work-detail\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)[^}]*width:\s*min\(var\(--mission-drawer-width\),\s*100%\)/s);
  expect(fileStyles).toMatch(/\.file-detail,\s*\.file-preview\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*box-shadow:\s*var\(--mission-shadow-drawer\)/s);
  expect(crewStyles).toMatch(/\.crew-dialog\s*\{[^}]*box-shadow:\s*var\(--mission-shadow-dialog\)/s);
  expect(workStyles).toMatch(/min-height:\s*var\(--mission-control-compact\)/s);
  expect(fileStyles).toMatch(/min-height:\s*var\(--mission-control-compact\)/s);
});

test("the Mission room uses disciplined proportions and content-fit messages", () => {
  expect(conversationStyles).toMatch(/\.mission-conversation-workspace\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/\.mission-room-header\s*\{[^}]*min-height:\s*3\.75rem/s);
  expect(conversationStyles).toMatch(/\.mission-room-layout\s*\{[^}]*grid-template-columns:\s*12rem\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/\.conversation-message\s*\{[^}]*position:\s*relative/s);
  expect(conversationStyles).toMatch(/\.conversation-message-actions\s*\{[^}]*position:\s*absolute/s);
  expect(crewStyles).toMatch(/\.mission-conversation-workspace\s+\.crew-quick-actions\s+button\s*\{[^}]*min-height:\s*1\.875rem/s);
});

test("the Mission room groups sparse conversation beside one compact command surface", () => {
  expect(conversationStyles).toMatch(/\.mission-conversation-workspace\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/\.mission-room-header\s*\{[^}]*grid-template-columns:\s*auto\s+minmax\(0,\s*1fr\)\s+auto/s);
  expect(conversationStyles).toMatch(/\.conversation-timeline\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/s);
  expect(conversationStyles).toMatch(/\.conversation-timeline-content\s*\{[^}]*margin-block-start:\s*auto/s);
  expect(conversationStyles).toMatch(/\.conversation-composer\s*\{[^}]*max-width:\s*48rem[^}]*padding:\s*0/s);
  expect(conversationStyles).toMatch(/\.composer-command\s*\{[^}]*border:\s*1px\s+solid\s+var\(--mission-color-border-strong\)/s);
  expect(conversationStyles).toMatch(/\.composer-input-wrap\s+textarea\s*\{[^}]*min-height:\s*2\.75rem[^}]*resize:\s*none/s);
  expect(crewStyles).toMatch(/\.crew-quick-actions\s*\{[^}]*display:\s*flex/s);
});

test("Mission work reads as one connected progress sequence with a restrained command edge", () => {
  expect(conversationStyles).toMatch(/\.conversation-message-wrap\.is-work-event:not\(:last-child\)::after\s*\{[^}]*background:\s*var\(--mission-color-border-strong\)/s);
  expect(conversationStyles).toMatch(/\.conversation-message\.kind-agent_completed\s*\{[^}]*background:\s*var\(--mission-color-surface-raised\)/s);
  expect(conversationStyles).toMatch(/\.conversation-work-link\.is-primary-action\s*\{[^}]*background:\s*var\(--mission-color-accent\)/s);
  expect(conversationStyles).toMatch(/\.composer-actions\s+>\s+button\.is-icon-only\s*\{[^}]*border-radius:\s*50%/s);
  expect(workplaceStyles).toMatch(/\.workplace-shell\s*\{[^}]*letter-spacing:\s*0/s);
  expect(workplaceStyles).toMatch(/\.workplace-shell\s+svg\s*\{[^}]*display:\s*block[^}]*flex:\s*none/s);
  expect(conversationStyles).toMatch(/\.conversation-message-body\s+time\s*\{[^}]*font-family:\s*var\(--mission-font-mono\)[^}]*font-variant-numeric:\s*tabular-nums/s);
});
