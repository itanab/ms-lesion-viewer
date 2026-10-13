import type { Capabilities, Job, MaskInfo, Measurements, Plane, StudyDetail, StudySummary } from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) {
    super(message);
  }
}

async function parseError(r: Response): Promise<ApiError> {
  try {
    const j = await r.json();
    return new ApiError(r.status, j.error.code, j.error.message, j.error.details);
  } catch {
    return new ApiError(r.status, "HTTP_ERROR", `Request failed (${r.status}).`);
  }
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + url, init);
  if (!r.ok) throw await parseError(r);
  return (await r.json()) as T;
}

export const api = {
  capabilities: () => json<Capabilities>("/capabilities"),
  listStudies: () => json<{ items: StudySummary[]; next_cursor: string | null }>("/studies?limit=100"),
  getStudy: (id: string) => json<StudyDetail>(`/studies/${id}`),
  createStudy: (form: FormData) => json<{ id: string }>("/studies", { method: "POST", body: form }),
  createDemo: () => json<{ id: string }>("/demo", { method: "POST" }),
  deleteStudy: async (id: string) => {
    const r = await fetch(`${BASE}/studies/${id}`, { method: "DELETE" });
    if (!r.ok) throw await parseError(r);
  },
  getMask: (s: string, m: string) => json<MaskInfo>(`/studies/${s}/masks/${m}`),
  uploadMask: (id: string, file: File, acceptOffset: boolean) => {
    const f = new FormData();
    f.append("mask", file);
    if (acceptOffset) f.append("accept_subvoxel_offset", "true");
    return json<{ id: string }>(`/studies/${id}/masks`, { method: "POST", body: f });
  },
  createJob: (id: string, modelId: string) =>
    json<Job>(`/studies/${id}/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_id: modelId }) }),
  getJob: (id: string) => json<Job>(`/jobs/${id}`),
  measurements: (s: string, m: string, plane: Plane, idx: number, rev: number, signal?: AbortSignal) =>
    json<Measurements>(`/studies/${s}/masks/${m}/slices/${plane}/${idx}/measurements?display_revision=${rev}`, { signal }),
  imageUrl: (s: string, plane: Plane, idx: number, rev: number, center: number, width: number) =>
    `${BASE}/studies/${s}/slices/${plane}/${idx}/image?display_revision=${rev}&center=${center}&width=${width}`,
  maskUrl: (s: string, m: string, plane: Plane, idx: number, rev: number) =>
    `${BASE}/studies/${s}/masks/${m}/slices/${plane}/${idx}?display_revision=${rev}`,
  downloadUrl: (s: string, m: string) => `${BASE}/studies/${s}/masks/${m}/download`,
  provenanceUrl: (s: string, m: string) => `${BASE}/studies/${s}/masks/${m}/provenance`,
};
