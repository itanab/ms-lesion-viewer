import type { StudySummary } from "../types";

interface Props {
  items: StudySummary[];
  currentId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

export function CaseRail({ items, currentId, onOpen, onDelete }: Props) {
  return (
    <nav className="rail" aria-label="Local cases">
      <div className="sec"><span>Cases</span></div>
      {items.length === 0 && <div className="muted small">No cases yet.</div>}
      {items.map((s) => (
        <div key={s.id} className={`case ${s.id === currentId ? "sel" : ""}`}>
          <button className="case-btn" onClick={() => onOpen(s.id)} aria-current={s.id === currentId}>
            <span className="case-name">{s.alias || (s.is_demo ? "Synthetic demo" : "Unnamed case")}</span>
            <span className="muted small">{new Date(s.created_at).toLocaleDateString()} · {s.state}</span>
          </button>
        </div>
      ))}
      {currentId && (
        <button className="del" onClick={() => onDelete(currentId)} aria-label="Delete selected case">Delete case</button>
      )}
    </nav>
  );
}
