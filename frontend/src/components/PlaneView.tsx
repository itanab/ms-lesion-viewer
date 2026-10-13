import { useEffect, useMemo, useRef, useState } from "react";
import { outlineOf } from "../frames";
import { clamp, cursorToRaster, fmt, rasterToCursor, rasterToScreen, screenToRaster, sliceCoordinateMm, sliceIndex, viewTransform } from "../geometry";
import type { Frame } from "../hooks";
import { useFrame } from "../hooks";
import type { Cursor, Footprint, Plane, StudyDetail } from "../types";

export interface ViewOptions {
  showMask: boolean;
  showDiameters: boolean;
  outline: boolean;
  crosshair: boolean;
  opacity: number; // 0..1, applied locally on the canvas: no request, no re-decode
}

interface Props {
  plane: Plane;
  study: StudyDetail;
  cursor: Cursor;
  maskId: string | null;
  opts: ViewOptions;
  window: { center: number; width: number } | null;
  active: boolean;
  compact?: boolean;
  resetToken: number;
  selectedLesion: number | null;
  onCursor: (c: Cursor, plane: Plane) => void;
  onScroll: (plane: Plane, delta: number) => void;
  onActivate: (plane: Plane) => void;
  onMeasurements?: (plane: Plane, index: number, fps: Footprint[] | null) => void;
}

const MAX_LABELS = 20;
const PLANE_TITLE: Record<Plane, string> = { axial: "Axial", coronal: "Coronal", sagittal: "Sagittal" };
const TEAL = "#5fe0cf";

export function PlaneView(p: Props) {
  const { plane, study } = p;
  const geom = study.display!.planes[plane];
  const shape = study.display!.shape;
  const index = sliceIndex(plane, p.cursor);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 300, h: 300 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<[number, number]>([0, 0]);
  const drag = useRef<null | { mode: "cursor" | "pan"; x: number; y: number; pan: [number, number] }>(null);

  const { frame, meas, measLoading, error } = useFrame({
    study, plane, index, maskId: p.maskId, showMask: p.opts.showMask, showDiameters: p.opts.showDiameters, window: p.window,
  });

  useEffect(() => { setZoom(1); setPan([0, 0]); }, [p.resetToken, study.id]);

  const report = p.onMeasurements;
  useEffect(() => {
    report?.(plane, index, meas && meas.available ? meas.footprints : null);
  }, [meas, plane, index, report]);

  useEffect(() => {
    const el = wrap.current!;
    const ro = new ResizeObserver(() => setSize({ w: Math.max(50, el.clientWidth), h: Math.max(50, el.clientHeight) }));
    ro.observe(el);
    setSize({ w: Math.max(50, el.clientWidth), h: Math.max(50, el.clientHeight) });
    return () => ro.disconnect();
  }, []);

  const t = useMemo(
    () => viewTransform(geom.width, geom.height, geom.pixel_spacing_mm, size.w, size.h, zoom, pan),
    [geom, size, zoom, pan],
  );

  // wheel: slice scroll; ctrl/cmd + wheel: zoom around the pointer. Needs a non-passive native listener.
  const latest = useRef({ t, zoom, pan, onScroll: p.onScroll });
  latest.current = { t, zoom, pan, onScroll: p.onScroll };
  useEffect(() => {
    const el = canvas.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const L = latest.current;
      if (e.ctrlKey || e.metaKey) {
        const rect = el.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const nz = clamp(L.zoom * Math.exp(-e.deltaY * 0.01), 1, 12);
        const k = nz / L.zoom;
        // keep the raster point under the pointer fixed
        const cx = size.w / 2, cy = size.h / 2;
        setPan([mx - cx - (mx - cx - L.pan[0]) * k, my - cy - (my - cy - L.pan[1]) * k]);
        setZoom(nz);
      } else if (e.deltaY !== 0) {
        L.onScroll(plane, e.deltaY > 0 ? 1 : -1);
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [plane, size.w, size.h]);

  // ---- drawing -----------------------------------------------------------------------------
  useEffect(() => {
    const cv = canvas.current!;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(size.w * dpr);
    cv.height = Math.round(size.h * dpr);
    const g = cv.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#000";
    g.fillRect(0, 0, size.w, size.h);
    const f: Frame | null = frame && frame.studyId === study.id ? frame : null;
    if (f) {
      const w = geom.width * t.px, h = geom.height * t.py;
      g.imageSmoothingEnabled = true;
      g.drawImage(f.image, t.x0, t.y0, w, h);
      if (p.opts.showMask && f.mask && f.mask.maskId === p.maskId) {
        g.imageSmoothingEnabled = false; // nearest-neighbour for labels
        g.globalAlpha = p.opts.opacity;
        g.drawImage(p.opts.outline ? outlineOf(f.mask.bitmap) : f.mask.bitmap, t.x0, t.y0, w, h);
        g.globalAlpha = 1;
      }
    }
    if (p.opts.crosshair) {
      const [u, v] = cursorToRaster(plane, p.cursor, shape);
      const [sx, sy] = rasterToScreen(t, u, v);
      g.save();
      g.strokeStyle = "rgba(232,194,90,0.85)";
      g.lineWidth = 1;
      g.setLineDash([4, 4]);
      g.beginPath(); g.moveTo(sx, 0); g.lineTo(sx, size.h); g.moveTo(0, sy); g.lineTo(size.w, sy); g.stroke();
      g.restore();
    }
    // diameter annotations share the exact same transform; text stays in screen space
    if (p.opts.showDiameters && meas && meas.available && f) drawDiameters(g, meas.footprints, t, p.selectedLesion, size.w > 420);
    // orientation letters
    g.fillStyle = "#c5cbd2";
    g.font = "12px system-ui, sans-serif";
    g.textAlign = "center"; g.textBaseline = "middle";
    const L = geom.edge_labels;
    g.fillText(L.left, 10, size.h / 2); g.fillText(L.right, size.w - 10, size.h / 2);
    g.fillText(L.top, size.w / 2, 12); g.fillText(L.bottom, size.w / 2, size.h - 12);
  }, [frame, meas, size, t, p.opts, p.cursor, p.maskId, p.selectedLesion, plane, geom, shape, study.id]);

  // ---- pointer -----------------------------------------------------------------------------
  const toCursor = (e: React.PointerEvent) => {
    const rect = canvas.current!.getBoundingClientRect();
    const [u, v] = screenToRaster(t, e.clientX - rect.left, e.clientY - rect.top);
    p.onCursor(rasterToCursor(plane, p.cursor, u, v, shape), plane);
  };
  const onDown = (e: React.PointerEvent) => {
    p.onActivate(plane);
    canvas.current!.setPointerCapture(e.pointerId);
    if (e.button === 1 || e.button === 2 || e.shiftKey) drag.current = { mode: "pan", x: e.clientX, y: e.clientY, pan };
    else { drag.current = { mode: "cursor", x: e.clientX, y: e.clientY, pan }; toCursor(e); }
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (d.mode === "pan") setPan([d.pan[0] + e.clientX - d.x, d.pan[1] + e.clientY - d.y]);
    else toCursor(e);
  };
  const onUp = () => { drag.current = null; };

  const coord = sliceCoordinateMm(geom.slice_origin_mm, geom.slice_step_mm, index);
  const hidden = p.opts.showDiameters && meas?.available ? Math.max(0, meas.footprints.length - MAX_LABELS) : 0;
  const zoomBy = (k: number) => { setZoom((z) => clamp(z * k, 1, 12)); };

  return (
    <div className={`pane ${p.active ? "active" : ""}`} ref={wrap} data-plane={plane}
         data-frame-index={frame && frame.studyId === study.id ? frame.key.split("/slices/")[1]?.split("/")[1] : ""}
         data-mask-drawn={p.opts.showMask && frame?.mask?.maskId === p.maskId ? "true" : "false"}
         data-diameters-drawn={p.opts.showDiameters && meas?.available ? String(Math.min(meas.footprints.length, MAX_LABELS)) : "0"}>
      <canvas
        ref={canvas}
        style={{ width: size.w, height: size.h }}
        role="img"
        aria-label={`${PLANE_TITLE[plane]} slice ${index + 1} of ${geom.slice_count}`}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
        onContextMenu={(e) => e.preventDefault()}
      />
      <span className="pill pill-tl">
        {PLANE_TITLE[plane]} · {index + 1} / {geom.slice_count} · {geom.slice_axis} {fmt(coord)} mm
      </span>
      <div className="pane-tools">
        <button aria-label={`Zoom in ${plane}`} onClick={() => zoomBy(1.25)}>+</button>
        <button aria-label={`Zoom out ${plane}`} onClick={() => zoomBy(0.8)}>−</button>
      </div>
      {p.opts.showDiameters && measLoading && <span className="pill pill-bl">Measuring…</span>}
      {p.opts.showDiameters && meas && !meas.available && <span className="pill pill-bl warn">{meas.reason ?? "Measurement unavailable"}</span>}
      {hidden > 0 && <span className="pill pill-bl">{hidden} labels hidden · see list</span>}
      {error && <span className="pill pill-br warn">{error}</span>}
    </div>
  );
}

function drawDiameters(g: CanvasRenderingContext2D, fps: Footprint[], t: ReturnType<typeof viewTransform>, selected: number | null, long: boolean) {
  const sorted = [...fps].sort((a, b) => b.diameter_mm - a.diameter_mm);
  const shown = sorted.slice(0, MAX_LABELS);
  for (const s of sorted) if (s.candidate_id === selected && !shown.includes(s)) shown.push(s);
  g.font = "11px system-ui, sans-serif";
  g.textAlign = "left"; g.textBaseline = "middle";
  for (const f of shown) {
    const hot = f.candidate_id === selected;
    const col = hot ? "#ffd24a" : TEAL;
    const [x0, y0] = rasterToScreen(t, f.endpoints_raster[0][0], f.endpoints_raster[0][1]);
    const [x1, y1] = rasterToScreen(t, f.endpoints_raster[1][0], f.endpoints_raster[1][1]);
    // endpoints use the backend raster convention (pixel centres integer, cell edges half-integer): no extra offset
    const ex0 = x0, ey0 = y0, ex1 = x1, ey1 = y1;
    g.strokeStyle = col; g.fillStyle = col; g.lineWidth = hot ? 2 : 1.25;
    g.beginPath(); g.moveTo(ex0, ey0); g.lineTo(ex1, ey1); g.stroke();
    for (const [x, y] of [[ex0, ey0], [ex1, ey1]]) { g.beginPath(); g.arc(x, y, 2.5, 0, Math.PI * 2); g.fill(); }
    const text = `${long ? "Lesion " : "L"}${f.candidate_id} · ${f.diameter_mm.toFixed(1)} mm`;
    const tw = g.measureText(text).width + 10;
    const lx = (ex0 + ex1) / 2 + 8, ly = (ey0 + ey1) / 2 - 12;
    g.fillStyle = "rgba(12,14,17,0.85)";
    g.fillRect(lx, ly - 8, tw, 16);
    g.fillStyle = col;
    g.fillText(text, lx + 5, ly);
  }
}
