import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { cached, loadBitmap } from "./frames";
import type { Footprint, Plane, StudyDetail } from "./types";

export interface Frame {
  key: string;
  studyId: string;
  image: ImageBitmap;
  mask: { maskId: string; bitmap: ImageBitmap } | null;
}

export interface FrameArgs {
  study: StudyDetail | null;
  plane: Plane;
  index: number;
  maskId: string | null;
  showMask: boolean;
  showDiameters: boolean;
  window: { center: number; width: number } | null;
}

/**
 * Loads the MRI slice (paired with the mask slice when the overlay is visible) and commits both at once.
 * The previous frame stays on screen while the next one loads. A generation token discards stale results;
 * a short delay coalesces rapid scrolling so only the latest index is requested.
 */
export function useFrame(a: FrameArgs) {
  const [frame, setFrame] = useState<Frame | null>(null);
  const [meas, setMeas] = useState<{ key: string; footprints: Footprint[]; available: boolean; reason?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const showMaskRef = useRef(a.showMask);
  showMaskRef.current = a.showMask;
  const study = a.study;
  const rev = study?.display_revision ?? 0;
  const win = a.window;

  const imgUrl = study && win && study.state === "ready" ? api.imageUrl(study.id, a.plane, a.index, rev, win.center, win.width) : null;
  const maskUrl = study && a.maskId ? api.maskUrl(study.id, a.maskId, a.plane, a.index, rev) : null;

  // never show another case's pixels
  useEffect(() => {
    setFrame(null);
    setMeas(null);
  }, [study?.id]);

  // image (+ mask when visible), committed together
  useEffect(() => {
    if (!imgUrl || !study) return;
    let current = true;
    const wantMask = !!maskUrl && showMaskRef.current;
    const ready = !!cached(imgUrl) && (!wantMask || !!cached(maskUrl!));
    const run = () => {
      Promise.all([loadBitmap(imgUrl), wantMask ? loadBitmap(maskUrl!) : Promise.resolve(null)])
        .then(([image, mask]) => {
          if (!current) return;
          setError(null);
          setFrame((prev) => ({
            key: imgUrl,
            studyId: study.id,
            image,
            mask: mask && a.maskId ? { maskId: a.maskId, bitmap: mask } : prev && prev.key === imgUrl ? prev.mask : null,
          }));
          // small look-ahead window for smooth scrolling
          const n = study.display!.planes[a.plane].slice_count;
          for (const d of [1, -1, 2, -2]) {
            const j = a.index + d;
            if (j < 0 || j >= n || !win) continue;
            loadBitmap(api.imageUrl(study.id, a.plane, j, rev, win.center, win.width)).catch(() => {});
            if (maskUrl && showMaskRef.current && a.maskId) loadBitmap(api.maskUrl(study.id, a.maskId, a.plane, j, rev)).catch(() => {});
          }
        })
        .catch((e: Error) => current && setError(e.message));
    };
    const t = ready ? null : window.setTimeout(run, 35);
    if (ready) run();
    return () => {
      current = false;
      if (t) window.clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imgUrl, a.maskId]);

  // mask toggled on after the image was committed without it
  useEffect(() => {
    if (!a.showMask || !maskUrl || !a.maskId || !frame || frame.key !== imgUrl) return;
    if (frame.mask?.maskId === a.maskId) return;
    let current = true;
    loadBitmap(maskUrl).then((bitmap) => {
      if (current) setFrame((f) => (f && f.key === imgUrl ? { ...f, mask: { maskId: a.maskId!, bitmap } } : f));
    }).catch(() => {});
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.showMask, maskUrl, frame?.key]);

  // measurements: tied to the exact mask/plane/index; old lines are never shown for a different result
  const measKey = study && a.maskId ? `${study.id}|${rev}|${a.maskId}|${a.plane}|${a.index}` : null;
  useEffect(() => {
    setMeas((m) => (m && m.key === measKey ? m : null));
    if (!measKey || !a.showDiameters || !study || !a.maskId) return;
    let current = true;
    api.measurements(study.id, a.maskId, a.plane, a.index, rev)
      .then((r) => current && setMeas({ key: measKey, footprints: r.footprints, available: r.available, reason: r.reason }))
      .catch(() => current && setMeas({ key: measKey, footprints: [], available: false, reason: "Measurement unavailable." }));
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measKey, a.showDiameters]);

  return { frame, meas: meas && meas.key === measKey ? meas : null, measLoading: !!measKey && a.showDiameters && !(meas && meas.key === measKey), error };
}

/** Poll `fn` every `ms` while `active`; slower when the tab is hidden; stops on unmount. */
export function usePoll(fn: () => Promise<void>, active: boolean, ms = 1000) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    let stop = false;
    let t: number;
    const tick = async () => {
      try { await ref.current(); } catch { /* keep polling */ }
      if (!stop) t = window.setTimeout(tick, document.hidden ? ms * 4 : ms);
    };
    t = window.setTimeout(tick, ms);
    return () => { stop = true; window.clearTimeout(t); };
  }, [active, ms]);
}
