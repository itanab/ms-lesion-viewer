import { useRef } from "react";

interface Props {
  load: number[];
  index: number;
  onChange: (i: number) => void;
  label: string;
}

/** Slice scrubber drawn as a histogram of lesion area per slice. */
export function LoadStrip({ load, index, onChange, label }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const max = Math.max(1, ...load);
  const n = load.length;
  const pick = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    onChange(Math.min(n - 1, Math.max(0, Math.floor(((clientX - r.left) / r.width) * n))));
  };
  return (
    <div className="scrub">
      <div
        ref={ref}
        className="strip"
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={1}
        aria-valuemax={n}
        aria-valuenow={index + 1}
        aria-valuetext={`Slice ${index + 1} of ${n}, ${load[index] ?? 0} lesion pixels`}
        onPointerDown={(e) => { (e.target as Element).setPointerCapture?.(e.pointerId); pick(e.clientX); }}
        onPointerMove={(e) => { if (e.buttons === 1) pick(e.clientX); }}
        onKeyDown={(e) => {
          // same convention as the global shortcuts: up/left = previous slice, down/right = next slice
          if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); onChange(Math.min(n - 1, index + 1)); }
          if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); onChange(Math.max(0, index - 1)); }
          if (e.key === "Home") onChange(0);
          if (e.key === "End") onChange(n - 1);
        }}
      >
        {load.map((v, i) => (
          <i key={i} className={i === index ? "cur" : ""} style={{ height: `${Math.max(v > 0 ? 6 : 2, (v / max) * 100)}%` }} />
        ))}
        <span className="head" style={{ left: `calc(${((index + 0.5) / n) * 100}% - 1px)` }} />
      </div>
      <div className="strip-row">
        <span>Lesion area per slice · click or drag to jump</span>
        <span>Slice {index + 1} / {n}</span>
      </div>
    </div>
  );
}
