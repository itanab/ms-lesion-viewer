/**
 * Pure viewer geometry. Mirrors the backend raster contract (backend/app/imaging/geometry.py):
 *   raster[row, col] = s[na-1-col, nb-1-row], pixel centres at integer coordinates, cell edges at half-integers.
 * Axial: axes (x,y) slice z. Coronal: axes (x,z) slice y. Sagittal: axes (y,z) slice x.
 */
import type { Cursor, Plane } from "./types";

export const PLANE_AXES: Record<Plane, [number, number, number]> = {
  axial: [0, 1, 2],
  coronal: [0, 2, 1],
  sagittal: [1, 2, 0],
};

export type Shape = [number, number, number];

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export function midpointCursor(shape: Shape): Cursor {
  return [Math.floor(shape[0] / 2), Math.floor(shape[1] / 2), Math.floor(shape[2] / 2)];
}

export function sliceIndex(plane: Plane, cur: Cursor): number {
  return cur[PLANE_AXES[plane][2]];
}

export function withSlice(plane: Plane, cur: Cursor, idx: number, shape: Shape): Cursor {
  const c = PLANE_AXES[plane][2];
  const next: Cursor = [...cur];
  next[c] = clamp(Math.round(idx), 0, shape[c] - 1);
  return next;
}

/** Cursor -> raster coordinate (u = column, v = row) of the crosshair in this plane. */
export function cursorToRaster(plane: Plane, cur: Cursor, shape: Shape): [number, number] {
  const [a, b] = PLANE_AXES[plane];
  return [shape[a] - 1 - cur[a], shape[b] - 1 - cur[b]];
}

/** Raster click -> new cursor (the slice axis is unchanged). */
export function rasterToCursor(plane: Plane, cur: Cursor, u: number, v: number, shape: Shape): Cursor {
  const [a, b] = PLANE_AXES[plane];
  const next: Cursor = [...cur];
  next[a] = clamp(shape[a] - 1 - Math.round(u), 0, shape[a] - 1);
  next[b] = clamp(shape[b] - 1 - Math.round(v), 0, shape[b] - 1);
  return next;
}

export interface ViewTransform {
  /** screen position of the raster's top-left corner (edge of pixel 0, i.e. raster coordinate -0.5) */
  x0: number;
  y0: number;
  /** screen size of one raster pixel (anisotropic when spacings differ) */
  px: number;
  py: number;
}

/** Fit a width x height raster of physical pixel size (sc x sr mm) into a view, then apply zoom and pan. */
export function viewTransform(
  width: number, height: number, spacing: [number, number],
  viewW: number, viewH: number, zoom: number, pan: [number, number],
): ViewTransform {
  const physW = width * spacing[0];
  const physH = height * spacing[1];
  const fit = Math.min(viewW / physW, viewH / physH);
  const px = fit * spacing[0] * zoom;
  const py = fit * spacing[1] * zoom;
  return { px, py, x0: viewW / 2 - (width * px) / 2 + pan[0], y0: viewH / 2 - (height * py) / 2 + pan[1] };
}

export function rasterToScreen(t: ViewTransform, u: number, v: number): [number, number] {
  return [t.x0 + (u + 0.5) * t.px, t.y0 + (v + 0.5) * t.py];
}

export function screenToRaster(t: ViewTransform, x: number, y: number): [number, number] {
  return [(x - t.x0) / t.px - 0.5, (y - t.y0) / t.py - 0.5];
}

/** Physical coordinate (mm) of the slice along its axis. */
export function sliceCoordinateMm(origin: number, step: number, index: number): number {
  return origin + index * step;
}

export function fmt(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "-" : v.toFixed(digits);
}
