import { expect, test } from "@playwright/test";
import { freshApp, MOCK, openStudy, openWithMask, pill, SLICES, stats, uploadMask, fx } from "./helpers";

test.use({ baseURL: MOCK });
const axial = '.pane[data-plane="axial"]';

test("A02 upload opens the middle axial slice and survives refresh", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  await expect(pill(page)).toContainText(`${SLICES / 2 + 1} / ${SLICES}`);
  await expect(page.getByText("Research POC — not for diagnostic use")).toBeVisible();
  await page.reload();
  await expect(pill(page)).toContainText(`${SLICES / 2 + 1} / ${SLICES}`, { timeout: 15_000 });
});

test("A03 wheel, keys and strip navigate consistently and clamp at the ends", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  const box = (await page.locator(`${axial} canvas`).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, 100); await page.mouse.wheel(0, 100); await page.mouse.wheel(0, 100);
  await expect(pill(page)).toContainText(`${SLICES / 2 + 4} / ${SLICES}`);
  await page.keyboard.press("ArrowUp"); await page.keyboard.press("ArrowUp");
  await expect(pill(page)).toContainText(`${SLICES / 2 + 2} / ${SLICES}`);
  const strip = page.getByRole("slider", { name: /axial slice/i });
  await strip.focus();
  await page.keyboard.press("End");
  await expect(pill(page)).toContainText(`${SLICES} / ${SLICES}`);
  await page.keyboard.press("ArrowRight");
  await expect(pill(page)).toContainText(`${SLICES} / ${SLICES}`);        // clamped at the last slice
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowLeft");
  await expect(pill(page)).toContainText(`1 / ${SLICES}`);                // clamped at the first slice
});

test("A05 mask is aligned: the lesion under the crosshair is orange in every plane", async ({ page }) => {
  await freshApp(page, MOCK);
  await openWithMask(page);
  await page.getByLabel("Mask opacity").fill("100");
  await page.locator(".lt tbody tr").first().click();
  for (const plane of ["axial", "coronal", "sagittal"]) {
    await expect(page.locator(`.pane[data-plane="${plane}"]`)).toHaveAttribute("data-mask-drawn", "true");
    await expect.poll(async () => (await stats(page, plane)).nearOrange, { timeout: 10_000 }).toBe(true);
  }
});

test("A10 opacity and mask toggle never refetch slices", async ({ page }) => {
  await freshApp(page, MOCK);
  await openWithMask(page);
  await page.locator(".lt tbody tr").first().click();
  await expect(page.locator(axial)).toHaveAttribute("data-mask-drawn", "true");
  await page.waitForTimeout(800);
  const urls: string[] = [];
  page.on("request", (r) => /\/slices\//.test(r.url()) && urls.push(r.url()));
  const slider = page.getByLabel("Mask opacity");
  await slider.fill("100");
  const full = (await stats(page)).orange;
  await slider.fill("0");
  const none = (await stats(page)).orange;
  await slider.fill("40");
  await page.getByRole("button", { name: "Hide mask" }).click();
  await page.getByRole("button", { name: "Show mask" }).click();
  expect(full).toBeGreaterThan(20);
  expect(none).toBe(0);
  expect(urls).toEqual([]);
});

test("A21 mask and diameter toggles are independent (all four combinations)", async ({ page }) => {
  await freshApp(page, MOCK);
  await openWithMask(page);
  await page.getByLabel("Mask opacity").fill("100");
  await page.locator(".lt tbody tr").first().click();          // jump to the lesion ...
  await page.locator(".lt tbody tr").first().click();          // ... then deselect so lines use the normal teal colour
  await expect(page.locator(axial)).toHaveAttribute("data-diameters-drawn", /[1-9]/);
  const maskBtn = () => page.getByRole("button", { name: /(Hide|Show) mask/ });
  const diamBtn = () => page.getByRole("button", { name: /(Hide|Show) diameters/ });
  for (const [mask, diam] of [[true, true], [false, true], [true, false], [false, false]]) {
    if ((await maskBtn().innerText()).startsWith("Hide") !== mask) await maskBtn().click();
    if ((await diamBtn().innerText()).startsWith("Hide") !== diam) await diamBtn().click();
    await expect.poll(async () => { const s = await stats(page); return [s.orange > 0, s.teal > 0]; }, { timeout: 8_000 }).toEqual([mask, diam]);
  }
});

test("A12 stale slice responses never overwrite a newer slice", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  // earlier requests answer later: the last one requested is the fastest
  await page.route(/\/slices\/axial\/\d+\/image/, async (route) => {
    const idx = +/axial\/(\d+)\//.exec(route.request().url())![1];
    await new Promise((r) => setTimeout(r, Math.max(0, (SLICES / 2 + 12 - idx) * 120)));
    await route.continue();
  });
  const box = (await page.locator(`${axial} canvas`).boundingBox())!;
  await page.mouse.move(box.x + 50, box.y + 50);
  for (let i = 0; i < 6; i++) { await page.keyboard.press("ArrowDown"); }
  const target = SLICES / 2 + 6;
  await expect(page.locator(axial)).toHaveAttribute("data-frame-index", String(target), { timeout: 20_000 });
  await page.waitForTimeout(2500);                                        // late stragglers must not replace it
  await expect(page.locator(axial)).toHaveAttribute("data-frame-index", String(target));
  await expect(pill(page)).toContainText(`${target + 1} / ${SLICES}`);
});

test("A25 dense slice: hidden labels stay available in the slice list", async ({ page }) => {
  await freshApp(page, MOCK);
  await openWithMask(page, "mask_dense.nii.gz");
  await page.locator(".lt tbody tr").first().click();
  await expect(page.locator(axial)).toHaveAttribute("data-diameters-drawn", "20", { timeout: 15_000 });
  await expect(page.getByText("10 labels hidden")).toBeVisible();
  const rows = page.getByRole("list", { name: "Footprints on this slice" }).getByRole("listitem");
  await expect(rows).toHaveCount(30);
  await rows.nth(29).click();
  await expect(rows.nth(29)).toHaveClass(/sel/);
});

test("mask failures show actionable messages; half-voxel offset needs explicit acceptance", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  await uploadMask(page, "mask_shifted.nii.gz");
  await expect(page.getByRole("alert")).toContainText("Mask must match the original MRI grid");
  await uploadMask(page, "mask_prob.nii.gz");
  await expect(page.getByRole("alert")).toContainText("exactly 0 or 1");
  await uploadMask(page, "mask_halfvoxel.nii.gz");
  await expect(page.getByRole("alert")).toContainText("half a voxel");
  await page.getByRole("button", { name: /Accept sub-voxel header offset/ }).click();
  await expect(page.locator(".lt tbody tr").first()).toBeVisible({ timeout: 30_000 });
  await uploadMask(page, "mask_empty.nii.gz");
  await expect(page.getByText("No labeled lesion voxels")).toBeVisible({ timeout: 15_000 });
});

test("invalid MRI files fail with a clear message", async ({ page }) => {
  await freshApp(page, MOCK);
  for (const [file, text] of [["corrupt.nii.gz", /truncated|corrupt/i], ["dicom_like.nii", /DICOM is not supported/]] as const) {
    await page.getByRole("button", { name: "Upload MRI" }).first().click();
    await page.getByLabel("MRI file").setInputFiles(fx(file));
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(text, { timeout: 15_000 });
    await page.getByRole("button", { name: "Delete this case" }).click();
    await page.getByRole("button", { name: "Delete case" }).last().click();
  }
});

test("delete needs confirmation and removes the case", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  await page.getByRole("button", { name: "Delete selected case" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(axial)).toBeVisible();
  await page.getByRole("button", { name: "Delete selected case" }).click();
  await page.getByRole("button", { name: "Delete case" }).last().click();
  await expect(page.getByText("Open a brain MRI")).toBeVisible();
});

test("mock inference job produces a visibly synthetic mask", async ({ page }) => {
  await freshApp(page, MOCK);
  await openStudy(page);
  const run = page.getByRole("button", { name: "Run segmentation" });
  await expect(run).toBeEnabled();
  await run.click();
  await expect(run).toBeDisabled();
  await expect(page.getByText(/Segmentation/).first()).toBeVisible();
  await expect(page.getByText("Synthetic data — not a real segmentation.")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".lt tbody tr").first()).toBeVisible();
});
