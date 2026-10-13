import { expect, test } from "@playwright/test";
import { freshApp, NONE, openStudy, openWithMask } from "./helpers";

test.use({ baseURL: NONE });

test("A01/A20 viewer and demo work without model or GPU; synthetic data stays labelled", async ({ page }) => {
  await freshApp(page, NONE);
  await page.getByRole("button", { name: "Open synthetic demo" }).click();
  await expect(page.locator('.pane[data-plane="axial"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Synthetic data — not a real segmentation.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Run segmentation" })).toBeDisabled();
  await expect(page.getByText("No segmentation model is configured").first()).toBeVisible();
  const href = await page.getByRole("link", { name: "Mask .nii.gz" }).getAttribute("href");
  const r = await page.request.get(href!);
  expect(r.headers()["content-disposition"]).toContain("SYNTHETIC");
});

test("A20 uploaded-mask workflow is fully usable with no adapter", async ({ page }) => {
  await freshApp(page, NONE);
  await openWithMask(page);
  await expect(page.getByRole("button", { name: "Run segmentation" })).toBeDisabled();
  await expect(page.getByRole("link", { name: "Provenance" })).toBeVisible();
});

test("opens an MRI without any mask", async ({ page }) => {
  await freshApp(page, NONE);
  await openStudy(page);
  await expect(page.getByText("Upload a mask to see lesion measurements.")).toBeVisible();
});
