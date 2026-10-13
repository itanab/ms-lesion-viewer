import { expect, type Page } from "@playwright/test";
import path from "node:path";

export const fx = (name: string) => path.join(__dirname, "fixtures", name);
export const SLICES = 40; // axial slices in the fixture
export const MOCK = "http://127.0.0.1:8100";
export const NONE = "http://127.0.0.1:8101";

export async function clearCases(page: Page, base: string) {
  const r = await page.request.get(`${base}/api/v1/studies?limit=100`);
  for (const s of (await r.json()).items) await page.request.delete(`${base}/api/v1/studies/${s.id}`);
}

export async function freshApp(page: Page, base: string) {
  await clearCases(page, base);
  await page.addInitScript(() => localStorage.clear());
  await page.goto(base + "/");
}

export async function uploadMri(page: Page, file = "mri.nii.gz", alias = "e2e") {
  await page.getByRole("button", { name: "Upload MRI" }).first().click();
  await page.getByLabel("MRI file").setInputFiles(fx(file));
  await page.getByPlaceholder("e.g. case-01").fill(alias);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
}

export async function openStudy(page: Page, file = "mri.nii.gz") {
  await uploadMri(page, file);
  await expect(page.locator('.pane[data-plane="axial"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.pane[data-plane="axial"]')).toHaveAttribute("data-frame-index", /\d+/, { timeout: 15_000 });
}

export async function uploadMask(page: Page, file = "mask.nii.gz") {
  await page.getByLabel("Mask file").setInputFiles(fx(file));
}

export async function openWithMask(page: Page, mask = "mask.nii.gz") {
  await openStudy(page);
  await uploadMask(page, mask);
  await expect(page.locator('.pane[data-plane="axial"]')).toHaveAttribute("data-mask-drawn", /true|false/);
  await expect(page.getByText("Whole scan (3D)")).toBeVisible();
  await expect(page.locator(".lt tbody tr").first()).toBeVisible({ timeout: 30_000 });
}

export const pill = (page: Page, plane = "axial") => page.locator(`.pane[data-plane="${plane}"] .pill-tl`);

export interface Px { orange: number; teal: number; yellowX: number; yellowY: number; nearOrange: boolean }

/** Count orange (mask) and teal (diameter) pixels and locate the crosshair in a pane's canvas. */
export async function stats(page: Page, plane = "axial"): Promise<Px> {
  return page.locator(`.pane[data-plane="${plane}"] canvas`).evaluate((cv: HTMLCanvasElement) => {
    const g = cv.getContext("2d")!;
    const { width: w, height: h } = cv;
    const d = g.getImageData(0, 0, w, h).data;
    let orange = 0, teal = 0;
    const col = new Uint32Array(w), row = new Uint32Array(h);
    const isOrange = (i: number) => d[i] > 200 && d[i + 1] > 95 && d[i + 1] < 165 && d[i + 2] < 60;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (isOrange(i)) orange++;
      if (Math.abs(d[i] - 95) < 25 && Math.abs(d[i + 1] - 224) < 25 && Math.abs(d[i + 2] - 207) < 25) teal++;
      if (d[i] > 190 && d[i + 1] > 150 && d[i + 1] < 215 && d[i + 2] < 140 && d[i] - d[i + 2] > 80) { col[x]++; row[y]++; }
    }
    const argmax = (a: Uint32Array) => a.reduce((b, v, i) => (v > a[b] ? i : b), 0);
    const yx = argmax(col), yy = argmax(row);
    let near = false;
    for (let dy = -14; dy <= 14 && !near; dy++) for (let dx = -14; dx <= 14; dx++) {
      const x = yx + dx, y = yy + dy;
      if (x < 0 || y < 0 || x >= w || y >= h) continue;
      if (isOrange((y * w + x) * 4)) { near = true; break; }
    }
    return { orange, teal, yellowX: yx, yellowY: yy, nearOrange: near };
  });
}
