export type Plane = "axial" | "coronal" | "sagittal";
export const PLANES: Plane[] = ["axial", "coronal", "sagittal"];

export interface PlaneGeometry {
  width: number;
  height: number;
  /** [column spacing, row spacing] in mm */
  pixel_spacing_mm: [number, number];
  slice_count: number;
  slice_axis: "x" | "y" | "z";
  slice_origin_mm: number;
  slice_step_mm: number;
  edge_labels: { left: string; right: string; top: string; bottom: string };
}

export interface Failure {
  code: string;
  message: string;
  can_override?: boolean;
}

export interface CandidateStat {
  candidate_id: number;
  voxel_count: number;
  volume_mm3: number;
  volume_ml: number;
  equivalent_sphere_diameter_mm: number;
  max_3d_diameter_mm: number;
  max_axial_diameter_mm: number | null;
  anchor: { display_index: [number, number, number]; ras_mm: [number, number, number] } | null;
}

export interface MaskStats {
  voxel_count: number;
  volume_ml: number;
  volume_mm3: number;
  candidate_count: number;
  empty: boolean;
  message: string | null;
  largest_lesion_ml: number | null;
  median_lesion_ml: number | null;
  largest_3d_diameter_mm: number | null;
  slices_with_lesions: Record<Plane, number>;
  size_distribution: { edges_ml: number[]; counts: number[] };
  slice_load?: Record<Plane, number[]>;
}

export interface MaskInfo {
  id: string;
  state: "preparing" | "ready" | "failed";
  source: "uploaded" | "model" | "synthetic_demo";
  synthetic: boolean;
  revision: number;
  created_at: string;
  failure: Failure | null;
  statistics?: MaskStats;
  candidates?: CandidateStat[];
  provenance?: { model?: { model_id: string; model_version: string } | null; notice?: string };
}

export interface StudySummary {
  id: string;
  alias: string | null;
  patient_alias: string | null;
  timepoint_label: string | null;
  scan_date: string | null;
  sequence_label: string;
  state: "preparing" | "ready" | "failed";
  is_demo: boolean;
  created_at: string;
  failure: Failure | null;
  selected_mask_id: string | null;
}

export interface StudyDetail extends StudySummary {
  upload_label: string | null;
  masks: MaskInfo[];
  file_bytes?: number;
  display_revision?: number;
  default_window?: { center: number; width: number; p1: number; p99: number };
  intensity_range?: { min: number; max: number };
  original?: {
    shape: [number, number, number];
    dtype: string;
    orientation: string;
    affine_source: string;
    spacing_mm: [number, number, number];
    header_spatial_unit: string;
  };
  display?: {
    shape: [number, number, number];
    affine: number[][];
    spacing_mm: [number, number, number];
    resampled: boolean;
    method: string;
    planes: Record<Plane, PlaneGeometry>;
  };
}

export interface Footprint {
  candidate_id: number;
  footprint_id: number;
  diameter_mm: number;
  pixel_count: number;
  endpoints_raster: [[number, number], [number, number]];
  endpoints_ras_mm: [number[], number[]];
  label_anchor_raster: [number, number];
}

export interface Measurements {
  plane: Plane;
  index: number;
  mask_id: string;
  mask_revision: number;
  display_revision: number;
  available: boolean;
  reason?: string;
  footprints: Footprint[];
}

export interface Capabilities {
  app: string;
  notice: string;
  limits: { max_upload_bytes: number };
  inference: { available: boolean; reason: string | null; models: { model_id: string; version: string; synthetic: boolean }[] };
  demo: { available: boolean; label: string };
}

export interface Job {
  id: string;
  state: "queued" | "running" | "succeeded" | "failed";
  stage: string | null;
  error: { code: string; message: string } | null;
  output_mask_id: string | null;
}

/** Display-grid cursor [i, j, k] = indices along x, y, z (RAS+). */
export type Cursor = [number, number, number];
