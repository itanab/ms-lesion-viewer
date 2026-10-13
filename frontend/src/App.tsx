import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { clearFrames } from "./frames";
import { midpointCursor, sliceIndex, withSlice } from "./geometry";
import { usePoll } from "./hooks";
import { CaseRail } from "./components/CaseRail";
import { ConfirmDelete, UploadDialog, type UploadForm } from "./components/Dialogs";
import { LoadStrip } from "./components/LoadStrip";
import { PlaneView, type ViewOptions } from "./components/PlaneView";
import { SidePanel } from "./components/SidePanel";
import type { CandidateStat, Capabilities, Cursor, Footprint, Job, MaskInfo, Plane, StudyDetail, StudySummary } from "./types";
import { PLANES } from "./types";

const PREF_KEY = "mslv.prefs.v1";
interface Prefs { showMask: boolean; showDiameters: boolean; outline: boolean; crosshair: boolean; opacity: number; layout: "three" | "single"; rail: boolean }
const DEFAULT_PREFS: Prefs = { showMask: true, showDiameters: true, outline: false, crosshair: true, opacity: 0.4, layout: "three", rail: true };

function loadPrefs(): Prefs {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREF_KEY) ?? "{}") }; } catch { return DEFAULT_PREFS; }
}

export function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [cases, setCases] = useState<StudySummary[]>([]);
  const [study, setStudy] = useState<StudyDetail | null>(null);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [active, setActiveState] = useState<Plane>("axial");
  const setActive = useCallback((pl: Plane) => { activeRef.current = pl; setActiveState(pl); }, []);
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [winOverride, setWinOverride] = useState<{ center: number; width: number } | null>(null);
  const [applied, setApplied] = useState<{ center: number; width: number } | null>(null);
  const [selLesion, setSelLesion] = useState<number | null>(null);
  const [maskPick, setMaskPick] = useState<string | null>(null);
  const [maskDetail, setMaskDetail] = useState<MaskInfo | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [status, setStatus] = useState("Ready");
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [pendingMask, setPendingMask] = useState<{ id: string; file: File } | null>(null);
  const [maskError, setMaskError] = useState<{ message: string; canOverride: boolean; file?: File } | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [slice, setSlice] = useState<{ plane: Plane; index: number; footprints: Footprint[] | null }>({ plane: "axial", index: 0, footprints: null });
  const activeRef = useRef<Plane>("axial");
  const onMeasurements = useCallback((plane: Plane, index: number, fps: Footprint[] | null) => {
    if (activeRef.current === plane) setSlice({ plane, index, footprints: fps });
  }, []);

  const setPref = <K extends keyof Prefs>(k: K, v: Prefs[K]) =>
    setPrefs((p) => {
      const n = { ...p, [k]: v };
      try { localStorage.setItem(PREF_KEY, JSON.stringify(n)); } catch { /* storage unavailable: fine */ }
      return n;
    });

  // ---- data ---------------------------------------------------------------------------------
  const refreshCases = useCallback(async () => { setCases((await api.listStudies()).items); }, []);
  const refreshStudy = useCallback(async (id: string) => {
    const s = await api.getStudy(id);
    setStudy((cur) => (cur && cur.id !== id && !s ? cur : s));
    return s;
  }, []);

  const openStudy = useCallback(async (id: string) => {
    clearFrames();
    setStudy(null); setCursor(null); setWinOverride(null); setApplied(null); setSelLesion(null);
    setMaskPick(null); setMaskDetail(null); setMaskError(null); setPendingMask(null); setJob(null);
    try { await refreshStudy(id); setStatus("Case opened"); } catch (e) { setStatus((e as Error).message); }
  }, [refreshStudy]);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setStatus("Cannot reach the server. Is the API running?"));
    refreshCases().then(() => undefined).catch(() => undefined);
  }, [refreshCases]);

  // reopen the most recent case on first load
  const [bootstrapped, setBootstrapped] = useState(false);
  useEffect(() => {
    if (!bootstrapped && cases.length) { setBootstrapped(true); void openStudy(cases[0].id); }
  }, [cases, bootstrapped, openStudy]);

  const busyStudy = !!study && (study.state === "preparing" || study.masks.some((m) => m.state === "preparing"));
  const jobActive = !!job && (job.state === "queued" || job.state === "running");
  usePoll(async () => {
    if (study) { await refreshStudy(study.id); }
    if (job && jobActive) {
      const j = await api.getJob(job.id);
      setJob(j);
      if (j.state === "succeeded" && j.output_mask_id) { setMaskPick(j.output_mask_id); setStatus("Segmentation finished"); }
      if (j.state === "failed") setMaskError({ message: j.error?.message ?? "Segmentation failed.", canOverride: false });
    }
  }, busyStudy || jobActive, 1000);

  // cursor starts at the integer midpoint of the display grid
  useEffect(() => {
    if (study?.state === "ready" && study.display && !cursor) setCursor(midpointCursor(study.display.shape));
  }, [study, cursor]);

  // keep the case list's status fresh
  useEffect(() => { if (study) refreshCases().catch(() => undefined); }, [study?.state, study?.masks.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const maskId = useMemo(() => {
    if (!study) return null;
    const ready = study.masks.filter((m) => m.state === "ready");
    if (maskPick && ready.some((m) => m.id === maskPick)) return maskPick;
    return study.selected_mask_id && ready.some((m) => m.id === study.selected_mask_id) ? study.selected_mask_id : null;
  }, [study, maskPick]);

  // a newly ready mask turns the overlay and diameters on at the default 40% opacity
  useEffect(() => {
    if (!maskId) return;
    setPrefs((p) => {
      const n = { ...p, showMask: true, showDiameters: true, opacity: 0.4 };
      try { localStorage.setItem(PREF_KEY, JSON.stringify(n)); } catch { /* ignore */ }
      return n;
    });
  }, [maskId]);

  useEffect(() => {
    setMaskDetail(null);
    setSelLesion(null);
    if (!study || !maskId) return;
    let cur = true;
    api.getMask(study.id, maskId).then((m) => cur && setMaskDetail(m)).catch(() => undefined);
    return () => { cur = false; };
  }, [study?.id, maskId]); // eslint-disable-line react-hooks/exhaustive-deps

  // outcome of a mask the user just uploaded
  useEffect(() => {
    if (!pendingMask || !study) return;
    const m = study.masks.find((x) => x.id === pendingMask.id);
    if (!m || m.state === "preparing") return;
    if (m.state === "ready") { setMaskPick(m.id); setMaskError(null); setStatus("Mask ready"); }
    else setMaskError({ message: m.failure?.message ?? "The mask was rejected.", canOverride: !!m.failure?.can_override, file: pendingMask.file });
    setPendingMask(null);
  }, [study, pendingMask]);

  // ---- window / level -------------------------------------------------------------------------
  const win = winOverride ?? (study?.default_window ? { center: study.default_window.center, width: study.default_window.width } : null);
  useEffect(() => {
    if (!win) return;
    const t = setTimeout(() => setApplied(win), applied ? 120 : 0); // debounce slider drags
    return () => clearTimeout(t);
  }, [win?.center, win?.width]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- actions -----------------------------------------------------------------------------------
  const scroll = useCallback((plane: Plane, delta: number) => {
    if (!study?.display) return;
    setCursor((c) => (c ? withSlice(plane, c, sliceIndex(plane, c) + delta, study.display!.shape) : c));
  }, [study]);

  const resetView = useCallback(() => {
    setResetToken((t) => t + 1); setWinOverride(null);
    setPrefs((p) => { const n = { ...p, opacity: 0.4, showMask: true, showDiameters: true }; try { localStorage.setItem(PREF_KEY, JSON.stringify(n)); } catch { /* ignore */ } return n; });
    setStatus("View reset");
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable], [role=slider]")) return;   // those handle their own keys
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowUp" || e.key === "PageUp") { e.preventDefault(); scroll(active, -1); }
      else if (e.key === "ArrowDown" || e.key === "PageDown") { e.preventDefault(); scroll(active, 1); }
      else if (e.key === "m" || e.key === "M") setPref("showMask", !prefs.showMask);
      else if (e.key === "d" || e.key === "D") setPref("showDiameters", !prefs.showDiameters);
      else if (e.key === "r" || e.key === "R") resetView();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, scroll, prefs.showMask, prefs.showDiameters, resetView]);

  const onUploadStudy = async (f: UploadForm) => {
    setUploading(true); setStatus("Uploading MRI…");
    try {
      const form = new FormData();
      form.append("image", f.file);
      if (f.alias) form.append("alias", f.alias);
      form.append("sequence_label", f.sequence);
      if (f.patient) form.append("patient_alias", f.patient);
      if (f.timepoint) form.append("timepoint_label", f.timepoint);
      if (f.date) form.append("scan_date", f.date);
      const { id } = await api.createStudy(form);
      setShowUpload(false);
      await refreshCases();
      await openStudy(id);
      setStatus("Preparing MRI…");
    } catch (e) { setStatus((e as ApiError).message); }
    finally { setUploading(false); }
  };

  const onDemo = async () => {
    setStatus("Creating synthetic demo…");
    try { const { id } = await api.createDemo(); await refreshCases(); await openStudy(id); } catch (e) { setStatus((e as Error).message); }
  };

  const onDelete = async () => {
    if (!study) return;
    setConfirmDel(false);
    try {
      await api.deleteStudy(study.id);
      clearFrames(); setStudy(null); setCursor(null);
      const items = (await api.listStudies()).items;
      setCases(items);
      setStatus("Case deleted");
      if (items.length) await openStudy(items[0].id);
    } catch (e) { setStatus((e as ApiError).message); }
  };

  const uploadMask = async (file: File, acceptOffset = false) => {
    if (!study) return;
    setMaskError(null); setStatus("Uploading mask…");
    try {
      const { id } = await api.uploadMask(study.id, file, acceptOffset);
      setPendingMask({ id, file });
      await refreshStudy(study.id);
    } catch (e) { setMaskError({ message: (e as ApiError).message, canOverride: false }); }
  };

  const runModel = async () => {
    if (!study || !caps?.inference.models[0]) return;
    try { setJob(await api.createJob(study.id, caps.inference.models[0].model_id)); setStatus("Segmentation queued"); }
    catch (e) { setMaskError({ message: (e as ApiError).message, canOverride: false }); }
  };

  const selectLesion = (c: CandidateStat) => {
    if (selLesion === c.candidate_id) { setSelLesion(null); return; }   // click again to deselect
    setSelLesion(c.candidate_id);
    if (c.anchor) setCursor(c.anchor.display_index);
  };

  // ---- render ------------------------------------------------------------------------------------
  const ready = study?.state === "ready" && !!study.display && !!cursor && !!win;
  const opts: ViewOptions = { showMask: prefs.showMask, showDiameters: prefs.showDiameters, outline: prefs.outline, crosshair: prefs.crosshair, opacity: prefs.opacity };
  const jobText = job && (job.state === "queued" ? "Segmentation queued…" : job.state === "running" ? `Segmentation: ${job.stage ?? "running"}…` : null);
  const view = (plane: Plane, compact = false) => (
    <PlaneView key={plane} plane={plane} study={study!} cursor={cursor!} maskId={maskId} opts={opts} window={applied} active={active === plane}
      compact={compact} resetToken={resetToken} selectedLesion={selLesion} onCursor={(c, pl) => { setCursor(c); setActive(pl); }}
      onScroll={scroll} onActivate={setActive} onMeasurements={onMeasurements} />
  );

  return (
    <div className="app">
      <header className="hdr">
        <button className="icon" aria-label="Toggle case list" aria-expanded={prefs.rail} onClick={() => setPref("rail", !prefs.rail)}>☰</button>
        <h1 className="brand">Neuro AI - MS Lesion Viewer</h1>
        {study && <span className="muted">{study.alias ?? (study.is_demo ? "Synthetic demo" : "")} · {study.sequence_label}</span>}
        <span className="grow" />
        <button onClick={() => setShowUpload(true)}>Upload MRI</button>
        <button onClick={onDemo}>Synthetic demo</button>
      </header>

      <div className={`body ${prefs.rail ? "" : "no-rail"}`}>
        {prefs.rail && <CaseRail items={cases} currentId={study?.id ?? null} onOpen={openStudy} onDelete={() => setConfirmDel(true)} />}

        <main className="main">
          {!study && (
            <div className="empty">
              <h2>Open a brain MRI</h2>
              <p>Upload one 3D NIfTI volume (.nii or .nii.gz), then add a binary lesion mask in the same space. DICOM, ZIP archives and 4D series are not supported.</p>
              <p className="muted">No model is needed: you can inspect any scan with an uploaded mask, or try the synthetic demo.</p>
              <div className="row-gap"><button className="primary" onClick={() => setShowUpload(true)}>Upload MRI</button><button onClick={onDemo}>Open synthetic demo</button></div>
            </div>
          )}
          {study?.state === "preparing" && <div className="empty"><h2>Preparing MRI…</h2><p className="muted">Validating the file and building the display grid.</p></div>}
          {study?.state === "failed" && (
            <div className="empty"><h2>This file could not be opened</h2><p role="alert">{study.failure?.message}</p>
              <button onClick={() => setConfirmDel(true)}>Delete this case</button></div>
          )}

          {ready && (
            <>
              <div className="tb" role="toolbar" aria-label="Viewer controls">
                {PLANES.map((pl) => (
                  <button key={pl} className={active === pl ? "on" : ""} aria-pressed={active === pl} onClick={() => setActive(pl)}>{pl[0].toUpperCase() + pl.slice(1)}</button>
                ))}
                <span className="sep" />
                <button aria-label="Previous slice" onClick={() => scroll(active, -1)}>‹</button>
                <button aria-label="Next slice" onClick={() => scroll(active, 1)}>›</button>
                <button onClick={() => setResetToken((t) => t + 1)}>Fit</button>
                <span className="sep" />
                <button className={prefs.showMask ? "on" : ""} aria-pressed={prefs.showMask} onClick={() => setPref("showMask", !prefs.showMask)} disabled={!maskId}>
                  {prefs.showMask ? "Hide mask" : "Show mask"}
                </button>
                <button className={prefs.showDiameters ? "on" : ""} aria-pressed={prefs.showDiameters} onClick={() => setPref("showDiameters", !prefs.showDiameters)} disabled={!maskId}
                        title="Mask and diameters are independent: hiding the mask keeps diameter lines visible while this is on.">
                  {prefs.showDiameters ? "Hide diameters" : "Show diameters"}
                </button>
                <button className={prefs.outline ? "on" : ""} aria-pressed={prefs.outline} onClick={() => setPref("outline", !prefs.outline)}>Outline</button>
                <button className={prefs.crosshair ? "on" : ""} aria-pressed={prefs.crosshair} onClick={() => setPref("crosshair", !prefs.crosshair)}>Crosshair</button>
                <span className="grow" />
                <button className={prefs.layout === "three" ? "on" : ""} aria-pressed={prefs.layout === "three"} onClick={() => setPref("layout", prefs.layout === "three" ? "single" : "three")}>
                  {prefs.layout === "three" ? "3 planes" : "Single"}
                </button>
                <button onClick={resetView}>Reset</button>
              </div>

              <div className={`views ${prefs.layout}`}>
                {prefs.layout === "three" ? (<>{view("axial")}{view("coronal", true)}{view("sagittal", true)}</>) : view(active)}
              </div>

              {maskDetail?.statistics?.slice_load ? (
                <LoadStrip load={maskDetail.statistics.slice_load[active]} index={sliceIndex(active, cursor!)}
                           label={`${active} slice with lesion load`} onChange={(i) => scroll(active, i - sliceIndex(active, cursor!))} />
              ) : (
                <div className="scrub"><input type="range" aria-label={`${active} slice`} min={0} max={study!.display!.planes[active].slice_count - 1}
                  value={sliceIndex(active, cursor!)} onChange={(e) => scroll(active, +e.target.value - sliceIndex(active, cursor!))} style={{ width: "100%" }} />
                  <div className="strip-row"><span>Slice</span><span>{sliceIndex(active, cursor!) + 1} / {study!.display!.planes[active].slice_count}</span></div></div>
              )}
            </>
          )}
        </main>

        {ready && (
          <SidePanel study={study!} mask={maskDetail} caps={caps} opacity={prefs.opacity} setOpacity={(v) => setPref("opacity", v)}
            win={win!} setWin={setWinOverride} resetWin={() => setWinOverride(null)} selected={selLesion} onSelectLesion={selectLesion}
            onPickMask={setMaskPick} onUploadMask={(f) => uploadMask(f)} onRetryOffset={() => maskError?.file && uploadMask(maskError.file, true)}
            maskError={maskError} onRunModel={runModel} jobText={jobText} slice={slice} />
        )}
      </div>

      <footer className="ft">
        <span className="disclaimer">⚠ Research POC — not for diagnostic use</span>
        <span className="status" role="status" aria-live="polite">{status}</span>
        <span className="muted">Wheel / ↑↓: slice · Ctrl+wheel: zoom · Shift+drag: pan · M mask · D diameters · R reset</span>
      </footer>

      {showUpload && <UploadDialog onClose={() => setShowUpload(false)} onSubmit={onUploadStudy} busy={uploading} maxBytes={caps?.limits.max_upload_bytes ?? 256 * 1048576} />}
      {confirmDel && study && <ConfirmDelete name={study.alias ?? "this case"} onConfirm={onDelete} onCancel={() => setConfirmDel(false)} />}
    </div>
  );
}
