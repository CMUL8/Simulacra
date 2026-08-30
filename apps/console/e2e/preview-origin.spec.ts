import { expect, test } from "@playwright/test";

test("preview_origin_exchange_preflight_cookie_nested_assets_and_external_form_submission_is_denied", async ({ page, context, request }) => {
  await page.goto("http://app.localhost:4173/__w0_harness__");

  // Node does not resolve *.localhost consistently across development machines.
  // Exercise the preview host contract explicitly while connecting to the local harness.
  const preflightResponse = await request.fetch("http://127.0.0.1:4174/preview/exchange", {
    method: "OPTIONS",
    headers: {
      Host: "preview.localhost:4174",
      Origin: "http://app.localhost:4173",
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type",
    },
  });
  const preflight = { status: preflightResponse.status(), origin: preflightResponse.headers()["access-control-allow-origin"], credentials: preflightResponse.headers()["access-control-allow-credentials"] };
  expect(preflight).toEqual({ status: 204, origin: "http://app.localhost:4173", credentials: "true" });

  await page.evaluate(async () => {
    const issued = await fetch("/projects/mission_preview/preview/exchanges", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((response) => response.json());
    const consumed = await fetch(`${issued.preview_origin}/preview/exchange`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exchange_id: issued.exchange_id, exchange_proof: issued.exchange_proof }),
    });
    if (consumed.status !== 204) throw new Error("Preview exchange failed");
    const dialog = document.createElement("section");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-label", "Verified preview dialog");
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close preview";
    const frame = document.createElement("iframe");
    frame.title = "Verified output";
    frame.sandbox.add("allow-scripts", "allow-forms", "allow-same-origin");
    frame.src = `${issued.preview_origin}/projects/mission_preview/preview/`;
    const endGuard = document.createElement("span");
    endGuard.tabIndex = 0;
    endGuard.dataset.previewFocusEnd = "true";
    endGuard.addEventListener("focus", () => close.focus());
    close.addEventListener("keydown", (event) => {
      if (event.key === "Tab" && event.shiftKey) {
        event.preventDefault();
        frame.focus();
      }
    });
    dialog.append(close, frame, endGuard);
    document.body.append(dialog);
  });

  const frame = page.frameLocator('iframe[title="Verified output"]');
  await expect(frame.getByText("Verified Mission output")).toBeVisible();
  await expect(frame.getByText("Nested asset loaded")).toBeVisible();

  const closePreview = page.getByRole("button", { name: "Close preview" });
  const frameAction = frame.getByRole("button", { name: "Send externally" });
  await frameAction.focus();
  await page.keyboard.press("Tab");
  await expect(closePreview).toBeFocused();
  await frameAction.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(closePreview).toBeFocused();
  await closePreview.focus();
  await page.keyboard.press("Shift+Tab");
  expect(await page.evaluate(() => (document.activeElement as HTMLIFrameElement | null)?.title)).toBe("Verified output");

  const cookies = await context.cookies();
  const previewCookie = cookies.find((cookie) => cookie.name === "mission_preview_w4");
  expect(previewCookie?.domain).toBe("preview.localhost");
  expect(previewCookie?.httpOnly).toBe(true);
  expect(previewCookie?.secure).toBe(true);

  const controlProbe = await page.evaluate(() => fetch("/__w4_control_cookie_probe__").then((response) => response.json()));
  expect(controlProbe.preview_cookie_seen).toBe(false);

  let externalRequest = false;
  page.on("request", (request) => { if (request.url().startsWith("https://external.invalid")) externalRequest = true; });
  await frame.getByRole("button", { name: "Send externally" }).click();
  await page.waitForTimeout(200);
  expect(externalRequest).toBe(false);
  await expect(frame.getByText("Verified Mission output")).toBeVisible();
});
