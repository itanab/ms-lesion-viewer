import { expect, test } from "@playwright/test";
import os from "node:os";
import { fx, freshApp, MOCK, uploadMask } from "./helpers";

// Run with: PERF=1 npx playwright test perf.spec.ts   (needs fixtures: make_fixtures.py <dir> --perf)
test.skip(!process.env.PERF, "performance check is opt-in");
test.use({ baseURL: MOCK });
const axial = '.pane[data-plane="axial"]';
const pct = (a: number[], p: number) => [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * p))];

test("performance targets on a 256x256x180 volume", async ({ page }) => {
  test.setTimeout(240_000);
  await freshApp(page, MOCK);
  const t0 = Date.now();
  await page.getByRole("button", { name: "Upload MRI" }).first().click();
  await page.getByLabel("MRI file").setInputFiles(fx("perf_mri.nii.gz"));
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.locator(axial)).toHaveAttribute("data-frame-index", /\d+/, { timeout: 60_000 });
  const ready = (Date.now() - t0) / 1000;
  const t1 = Date.now();
  await uploadMask(page, "perf_mask.nii.gz");
  await expect(page.locator(".lt tbody tr").first()).toBeVisible({ timeout: 60_000 });
  const maskReady = (Date.now() - t1) / 1000;

  const stepTo = async (key: string, expected: number) => {
    const t = Date.now();
    await page.keyboard.press(key);
    await expect(page.locator(axial)).toHaveAttribute("data-frame-index", String(expected), { timeout: 10_000 });
    return Date.now() - t;
  };
  await page.locator(axial).hover();
  const start = 90;
  const strip = page.getByRole("slider", { name: /axial slice/i });
  await strip.focus(); await page.keyboard.press("Home");
  for (let i = 0; i < start; i++) await page.keyboard.press("ArrowRight");
  await expect(page.locator(axial)).toHaveAttribute("data-frame-index", String(start));
  await page.locator(".pane-tools").first().hover(); await page.waitForTimeout(500);
  const steps: number[] = [];                                       // 100 sequential steps: look-ahead should hide latency
  for (let i = 1; i <= 80; i++) steps.push(await stepTo("ArrowDown", start + i));
  for (let i = 1; i <= 20; i++) steps.push(await stepTo("ArrowUp", start + 80 - i));
  const box = (await strip.boundingBox())!;
  const jumps: number[] = [];                                       // uncached random jumps (pairs when the mask is visible)
  const seen = new Set<number>();
  for (let i = 0; i < 40; i++) {
    let idx = Math.floor(Math.random() * 180);
    while (seen.has(idx) || Math.abs(idx - start) < 4) idx = Math.floor(Math.random() * 180);
    seen.add(idx);
    const t = Date.now();
    await page.mouse.click(box.x + ((idx + 0.5) / 180) * box.width, box.y + box.height / 2);
    await expect(page.locator(axial)).toHaveAttribute("data-frame-index", String(idx), { timeout: 10_000 });
    jumps.push(Date.now() - t);
  }
  const opacity = await page.getByLabel("Mask opacity").evaluate(async (el: HTMLInputElement) => {
    const times: number[] = [];
    for (const v of ["10", "70", "30", "100", "0", "40"]) {
      const t = performance.now();
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      times.push(performance.now() - t);
    }
    return Math.max(...times);
  });
  const report = {
    machine: `${os.cpus()[0].model}, ${os.cpus().length} cores, ${(os.totalmem() / 2 ** 30).toFixed(0)} GiB, ${os.platform()} ${os.arch()}`,
    browser: "Chromium (Playwright headless shell)", volume: "256x256x180 synthetic (float32), loopback",
    ready_to_view_s: ready, mask_analysis_s: maskReady,
    adjacent_step_ms: { p50: pct(steps, 0.5), p95: pct(steps, 0.95), max: Math.max(...steps) },
    uncached_jump_ms: { p50: pct(jumps, 0.5), p95: pct(jumps, 0.95), max: Math.max(...jumps) },
    opacity_change_ms_max: opacity,
  };
  console.log("PERF_REPORT " + JSON.stringify(report, null, 2));
});
