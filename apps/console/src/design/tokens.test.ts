import { expect, test } from "vitest";

import tokens from "./tokens.css?inline";

function tokenValue(name: string): string {
  const match = tokens.match(new RegExp(`${name}:\\s*([^;]+);`));
  if (!match) throw new Error(`Missing token ${name}`);
  return match[1].trim();
}

function luminance(hex: string): number {
  const channels = hex.slice(1).match(/.{2}/g)?.map((channel) => Number.parseInt(channel, 16) / 255);
  if (!channels || channels.length !== 3) throw new Error(`Expected an opaque hex color, received ${hex}`);
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

test("tokens_imports_and_meets_contrast_contract", () => {
  const foreground = tokenValue("--mission-color-fg");
  const canvas = tokenValue("--mission-color-canvas");
  const surface = tokenValue("--mission-color-surface");
  const contrast = (background: string) => (Math.max(luminance(foreground), luminance(background)) + 0.05) /
    (Math.min(luminance(foreground), luminance(background)) + 0.05);

  expect(tokens).toContain(":root");
  expect(tokens).toContain("color-scheme: light");
  expect(tokenValue("--mission-color-focus")).toMatch(/^#[0-9a-f]{6}$/i);
  expect(luminance(canvas)).toBeGreaterThan(luminance(foreground));
  expect(canvas).not.toBe(surface);
  expect(contrast(canvas)).toBeGreaterThanOrEqual(7);
  expect(contrast(surface)).toBeGreaterThanOrEqual(7);
});
