import { useRef, useState } from "react";

export function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDelete({ name, onConfirm, onCancel }: { name: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <Modal title="Delete case?" onClose={onCancel}>
      <p>
        This removes <b>{name}</b>, its masks, derived files and jobs from this computer. File deletion is not forensic secure erasure.
      </p>
      <div className="row-end">
        <button onClick={onCancel} autoFocus>Cancel</button>
        <button className="danger" onClick={onConfirm}>Delete case</button>
      </div>
    </Modal>
  );
}

export interface UploadForm {
  file: File;
  alias: string;
  sequence: string;
  patient: string;
  timepoint: string;
  date: string;
}

export function UploadDialog({ onSubmit, onClose, maxBytes, busy }: { onSubmit: (f: UploadForm) => void; onClose: () => void; maxBytes: number; busy: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ alias: "", sequence: "FLAIR", patient: "", timepoint: "", date: "" });
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const pick = (x: File | undefined) => {
    setErr(null);
    if (!x) return setFile(null);
    if (!/\.nii(\.gz)?$/i.test(x.name)) return setErr("Choose a .nii or .nii.gz file. DICOM and ZIP files are not supported.");
    if (x.size > maxBytes) return setErr(`That file is larger than the ${Math.round(maxBytes / 1048576)} MiB limit.`);
    setFile(x);
  };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title="Upload MRI" onClose={onClose}>
      <p className="muted">
        One 3D NIfTI volume (.nii or .nii.gz). Uploading does not anonymise a scan: use synthetic or de-identified data.
      </p>
      <div
        className="drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files[0]); }}
      >
        <input ref={input} type="file" accept=".nii,.gz" onChange={(e) => pick(e.target.files?.[0])} aria-label="MRI file" />
        {file && <div className="muted">{file.name} · {(file.size / 1048576).toFixed(1)} MiB</div>}
      </div>
      {err && <div className="alert" role="alert">{err}</div>}
      <div className="form-grid">
        <label>Case alias<input value={f.alias} onChange={set("alias")} placeholder="e.g. case-01" /></label>
        <label>Sequence (label only)
          <select value={f.sequence} onChange={set("sequence")}>
            <option>FLAIR</option><option>T1</option><option>T2</option><option>Other</option>
          </select>
        </label>
        <label>Patient alias (optional)<input value={f.patient} onChange={set("patient")} /></label>
        <label>Timepoint (optional)<input value={f.timepoint} onChange={set("timepoint")} placeholder="baseline" /></label>
        <label>Scan date (optional)<input type="date" value={f.date} onChange={set("date")} /></label>
      </div>
      <div className="row-end">
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!file || busy} onClick={() => file && onSubmit({ file, ...f })}>
          {busy ? "Uploading…" : "Upload"}
        </button>
      </div>
    </Modal>
  );
}
