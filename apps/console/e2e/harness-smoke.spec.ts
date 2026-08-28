import { expect, test } from "@playwright/test";

test("two_origin_frontend_harness_starts", async ({ page }) => {
  await page.goto("/__w0_harness__");
  await expect(page.locator("body")).toHaveAttribute("data-harness-role", "control");
  await expect(page.getByTitle("Preview harness")).toBeVisible();
  await expect(page.locator("[data-peer-message]")).toHaveText("preview-ready");
  await expect(page.getByTitle("Preview harness").contentFrame().locator("body")).toHaveAttribute(
    "data-harness-role",
    "preview",
  );
});
