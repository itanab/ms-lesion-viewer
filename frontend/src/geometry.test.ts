import { describe, expect, it } from "vitest";
import { cursorToRaster, midpointCursor, rasterToCursor, rasterToScreen, screenToRaster, sliceIndex, viewTransform, withSlice } from "./geometry";
import type { Plane } from "./types";

const shape: [number, number, number] = [40, 50, 30];

describe("cursor <-> raster", () => {
  it("round-trips in every plane", () => {
    for (const plane of ["axial", "coronal", "sagittal"] as Plane[]) {
      const cur: [number, number, number] = [7, 31, 12];
      const [u, v] = cursorToRaster(plane, cur, shape);
      expect(rasterToCursor(plane, [0, 0, 0], u, v, shape).slice(0, 3)).toEqual(
        plane === "axial" ? [7, 31, 0] : plane === "coronal" ? [7, 0, 12] : [0, 31, 12],
      );
    }
  });
  it("matches the radiological contract (left = +x, top = +y in axial)", () => {
    // highest x index (patient Right) must be at raster column 0 (left); highest y (Anterior) at row 0 (top)
    expect(cursorToRaster("axial", [39, 49, 0], shape)).toEqual([0, 0]);
    expect(cursorToRaster("axial", [0, 0, 0], shape)).toEqual([39, 49]);
    // coronal: top = Superior (+z); sagittal: left = Anterior (+y)
    expect(cursorToRaster("coronal", [0, 0, 29], shape)[1]).toBe(0);
    expect(cursorToRaster("sagittal", [0, 49, 0], shape)[0]).toBe(0);
  });
  it("clamps clicks and slices", () => {
    expect(rasterToCursor("axial", [5, 5, 5], -20, 900, shape)).toEqual([39, 0, 5]);
    expect(withSlice("axial", [1, 2, 3], 99, shape)[2]).toBe(29);
    expect(withSlice("coronal", [1, 2, 3], -4, shape)[1]).toBe(0);
    expect(sliceIndex("sagittal", [4, 5, 6])).toBe(4);
    expect(midpointCursor(shape)).toEqual([20, 25, 15]);
  });
});

describe("view transform", () => {
  it("preserves physical aspect ratio for anisotropic pixels", () => {
    const t = viewTransform(100, 50, [1, 3], 600, 600, 1, [0, 0]);
    expect(t.py / t.px).toBeCloseTo(3);
  });
  it("fits and centres; zoom and pan are shared linear transforms", () => {
    const t = viewTransform(100, 100, [1, 1], 400, 200, 1, [0, 0]);
    expect(t.px).toBeCloseTo(2);
    const [cx] = rasterToScreen(t, 49.5, 49.5);
    expect(cx).toBeCloseTo(200);
    const t2 = viewTransform(100, 100, [1, 1], 400, 200, 2, [10, -5]);
    expect(t2.px).toBeCloseTo(4);
    expect(rasterToScreen(t2, 49.5, 49.5)[0]).toBeCloseTo(210);
  });
  it("screen <-> raster is invertible and pixel edges sit at half-integers", () => {
    const t = viewTransform(64, 48, [0.7, 0.7], 500, 400, 1.7, [13, 9]);
    const [x, y] = rasterToScreen(t, 10.25, 7.5);
    const [u, v] = screenToRaster(t, x, y);
    expect(u).toBeCloseTo(10.25);
    expect(v).toBeCloseTo(7.5);
    expect(screenToRaster(t, t.x0, t.y0)[0]).toBeCloseTo(-0.5);
  });
});
