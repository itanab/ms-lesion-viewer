import { useRef, useState } from "react";
import { api } from "../api";
import { fmt } from "../geometry";
import type { CandidateStat, Capabilities, Footprint, MaskInfo, Plane, StudyDetail } from "../types";

interface Win { center: number; width: number }

export interface PanelProps {
  study: StudyDetail;
  mask: MaskInfo | null;
  caps: Capabilities | null;
  opacity: number;
  setOpacity: (v: number) => void;
  win: Win;
  setWin: (w: Win) => void;
  resetWin: () => void;
  selected: number | null;
  onSelectLesion: (c: CandidateStat) => void;
  onPickMask: (id: string) => void;
  onUploadMask: (file: File) => void;
  onRetryOffset: () => void;
  maskError: { message: string; canOverride: boolean } | null;
  onRunModel: () => void;
  jobText: string | null;
  slice: { plane: Plane; index: number; footprints: Footprint[] | null };
}

const BIN_LABELS = ["<.05", ".05–.2", ".2–.5", ".5–1", ">1"];

export function SidePanel(p: PanelProps) {
  const { study, mask } = p;
  const stats = mask?.statistics;
  const cands = mask?.candidates ?? [];
  const [all, setAll] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const rows = [...cands].sort((a, b) => b.volume_ml - a.volume_ml);
  const shown = all ? rows : rows.slice(0, 6);
  const range = study.intensity_range!;
  const dist = stats?.size_distribution.counts ?? [];
  const dmax = Math.max(1, ...dist);
  const inf = p.caps?.inference;
  const synthetic = mask?.synthetic || mask?.provenance?.model?.model_id === "mock-synthetic";

  return (
    <aside className="panel" aria-label="Details">
      <section>
        <div className="sec"><span>Whole scan (3D)</span><b>{stats ? `${stats.candidate_count} lesions` : "no mask"}</b></div>
        {stats ? (
          stats.empty ? <div className="muted">{stats.message}</div> : (
            <div className="kg">
              <Metric v={`${fmt(stats.volume_ml, 2)} mL`} l="total volume" />
              <Metric v={`${fmt(stats.largest_3d_diameter_mm)} mm`} l="largest 3D diameter" />
              <Metric v={`${fmt(stats.median_lesion_ml, 2)} mL`} l="median lesion" />
              <Metric v={`${stats.slices_with_lesions.axial} / ${study.display!.planes.axial.slice_count}`} l="axial slices with lesions" />
            </div>
          )
        ) : <div className="muted">Upload a mask to see lesion measurements.</div>}
      </section>

      {stats && !stats.empty && (
        <>
          <section>
            <div className="sec"><span>Lesions</span><span>sorted by volume</span></div>
            <table className="lt">
              <thead><tr><th>ID</th><th>mL</th><th title="Maximum 3D diameter (mm)">Ø 3D</th><th title="Maximum axial in-plane diameter over all slices (mm)">Ø ax</th></tr></thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.candidate_id} tabIndex={0} className={p.selected === c.candidate_id ? "sel" : ""}
                      onClick={() => p.onSelectLesion(c)}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), p.onSelectLesion(c))}>
                    <td>L{c.candidate_id}</td><td>{fmt(c.volume_ml, 2)}</td><td>{fmt(c.max_3d_diameter_mm)}</td><td>{fmt(c.max_axial_diameter_mm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="muted small">
              Ø in mm. {rows.length > 6 && <button className="link" onClick={() => setAll(!all)}>{all ? "show fewer" : `show all ${rows.length}`}</button>}
            </div>
          </section>
          <section>
            <div className="sec"><span>Size distribution</span><span>volume, mL</span></div>
            <div className="bars">{dist.map((n, i) => <i key={i} title={`${BIN_LABELS[i]}: ${n}`} style={{ height: `${(n / dmax) * 100}%` }} />)}</div>
            <div className="bar-labels">{BIN_LABELS.map((l) => <span key={l}>{l}</span>)}</div>
          </section>
        </>
      )}

      {stats && !stats.empty && (
        <section>
          <div className="sec"><span>This slice ({p.slice.plane} {p.slice.index + 1})</span><span>max in-plane Ø</span></div>
          {p.slice.footprints === null ? <div className="muted small">Measuring…</div>
            : p.slice.footprints.length === 0 ? <div className="muted small">No lesion on this slice.</div> : (
            <div className="slice-list" role="list" aria-label="Footprints on this slice">
              {[...p.slice.footprints].sort((a, b) => b.diameter_mm - a.diameter_mm).map((f) => (
                <button key={`${f.candidate_id}-${f.footprint_id}`} role="listitem"
                        className={`fp ${p.selected === f.candidate_id ? "sel" : ""}`}
                        onClick={() => { const c = cands.find((x) => x.candidate_id === f.candidate_id); if (c) p.onSelectLesion(c); }}>
                  <span>L{f.candidate_id}{f.footprint_id > 1 ? `·${f.footprint_id}` : ""}</span><span>{fmt(f.diameter_mm)} mm</span>
                </button>
              ))}
            </div>
          )}
          <div className="muted small">Maximum in-plane diameter (current slice); not a 3D diameter.</div>
        </section>
      )}

      <section>
        <div className="sec"><span>Display</span></div>
        <label className="rng">Mask opacity <b>{Math.round(p.opacity * 100)}%</b>
          <input type="range" min={0} max={100} value={Math.round(p.opacity * 100)} onChange={(e) => p.setOpacity(+e.target.value / 100)} />
        </label>
        <label className="rng">Window center
          <input type="number" value={Math.round(p.win.center)} onChange={(e) => p.setWin({ ...p.win, center: +e.target.value })} />
          <input type="range" min={range.min} max={range.max} value={p.win.center} onChange={(e) => p.setWin({ ...p.win, center: +e.target.value })} aria-label="Window center" />
        </label>
        <label className="rng">Window width
          <input type="number" min={1} value={Math.round(p.win.width)} onChange={(e) => +e.target.value > 0 && p.setWin({ ...p.win, width: +e.target.value })} />
          <input type="range" min={1} max={Math.max(2, (range.max - range.min) * 1.2)} value={p.win.width} onChange={(e) => p.setWin({ ...p.win, width: Math.max(1, +e.target.value) })} aria-label="Window width" />
        </label>
        <button onClick={p.resetWin}>Reset window</button>
      </section>

      <section>
        <div className="sec"><span>Mask</span>{mask && <span className="tag">{mask.source.replace("_", " ")}</span>}</div>
        {synthetic && <div className="alert amber">Synthetic data — not a real segmentation.</div>}
        {study.masks.filter((m) => m.state === "ready").length > 1 && (
          <select aria-label="Select mask" value={mask?.id} onChange={(e) => p.onPickMask(e.target.value)}>
            {study.masks.filter((m) => m.state === "ready").map((m, i) => <option key={m.id} value={m.id}>{`${i + 1}. ${m.source.replace("_", " ")}`}</option>)}
          </select>
        )}
        {study.masks.some((m) => m.state === "preparing") && <div className="muted">Preparing mask…</div>}
        {p.maskError && (
          <div className="alert" role="alert">
            {p.maskError.message}
            {p.maskError.canOverride && <div><button onClick={p.onRetryOffset}>Accept sub-voxel header offset and retry</button></div>}
          </div>
        )}
        <div className="row-gap">
          <input ref={file} type="file" accept=".nii,.gz" hidden aria-label="Mask file"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) p.onUploadMask(f); e.target.value = ""; }} />
          <button onClick={() => file.current?.click()}>Upload mask</button>
          <button disabled={!inf?.available || !!p.jobText} title={inf?.available ? "" : inf?.reason ?? ""}
                  onClick={p.onRunModel}>Run segmentation</button>
        </div>
        {!inf?.available && <div className="muted small">{inf?.reason}</div>}
        {p.jobText && <div className="muted small" aria-live="polite">{p.jobText}</div>}
        {mask?.state === "ready" && (
          <div className="row-gap">
            <a className="btn" href={api.downloadUrl(study.id, mask.id)} download>Mask .nii.gz</a>
            <a className="btn" href={api.provenanceUrl(study.id, mask.id)} download>Provenance</a>
          </div>
        )}
      </section>

      <section>
        <div className="sec"><span>Case</span></div>
        <KV k="Alias" v={study.alias ?? "-"} />
        <KV k="Sequence" v={`${study.sequence_label} (label)`} />
        {study.patient_alias && <KV k="Patient" v={study.patient_alias} />}
        {study.timepoint_label && <KV k="Timepoint" v={study.timepoint_label} />}
        <KV k="Scan date" v={study.scan_date ?? "not provided"} />
        <KV k="Added" v={new Date(study.created_at).toLocaleString()} />
      </section>
      <section>
        <div className="sec"><span>Image</span><span>from header</span></div>
        <KV k="Matrix" v={study.original!.shape.join(" × ")} />
        <KV k="Voxel" v={`${study.original!.spacing_mm.map((x) => fmt(x, 2)).join(" × ")} mm`} />
        <KV k="Orientation" v={study.original!.orientation} />
        <KV k="Affine" v={study.original!.affine_source} />
        <KV k="Data type" v={study.original!.dtype} />
        <KV k="Range" v={`${fmt(range.min, 0)} – ${fmt(range.max, 0)}`} />
        <KV k="File" v={`${((study.file_bytes ?? 0) / 1048576).toFixed(1)} MB`} />
        <KV k="Display grid" v={study.display!.resampled ? "resampled (isotropic)" : "reoriented, no resample"} />
      </section>
    </aside>
  );
}

const Metric = ({ v, l }: { v: string; l: string }) => <div className="k"><b>{v}</b><span>{l}</span></div>;
const KV = ({ k, v }: { k: string; v: string }) => <div className="kv"><span>{k}</span><span>{v}</span></div>;
