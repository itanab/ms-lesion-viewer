# Neuro AI - MS Lesion Viewer

A local browser app for reviewing a 3D brain MRI together with a binary multiple-sclerosis lesion mask:
three linked planes, mask overlay, per-lesion measurements (volumes, 3D and in-plane Feret diameters in mm),
export with provenance. **Research POC - not for diagnostic use.** Segmentation results are model outputs, not diagnoses.

* Backend: Python 3.11, FastAPI, NiBabel, SciPy, Pillow, SQLite (`backend/`)
* Frontend: React + TypeScript + Vite, canvas rendering (`frontend/`)
* Slices are rendered in Python (window/level to 8-bit PNG); the browser composes MRI, mask and measurement layers.
* No model, weights or GPU are needed: upload a mask, or open the synthetic demo.

## Install and run

Requirements: [uv](https://docs.astral.sh/uv/) (installs Python 3.11 for you). Node 20+ is optional; if it is missing the setup
script installs a project-local copy under `.tools/` (nothing system-wide).

```bash
scripts/setup.sh        # backend venv + frontend build (+ local Node if needed)
scripts/start.sh        # API + built frontend on http://127.0.0.1:8000 (loopback only)
```

Open <http://127.0.0.1:8000>, then **Upload MRI** or **Synthetic demo**.

Development (API auto-reload on :8000, Vite on :5173 with an `/api` proxy):

```bash
scripts/dev.sh          # open http://127.0.0.1:5173
```

Run the pieces separately:

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8000   # API (+ job worker thread)
cd frontend && npm run dev                                                                               # frontend
```

The job worker runs inside the API process (a persisted SQLite queue, one job at a time; every prediction runs in its own
child process so a timeout really kills it). Set `MSLV_RUN_WORKER=0` to disable it.

## Tests

```bash
cd backend  && .venv/bin/python -m pytest                          # 76 tests: geometry, validation, API, jobs
cd frontend && npm test                                            # viewer geometry (cursor <-> raster, transforms)
cd frontend && npm run build                                       # the e2e tests run against frontend/dist
cd tests/e2e && npm ci && npx playwright install chromium && npx playwright test    # 14 browser tests
cd tests/e2e && python ../../scripts/make_fixtures.py fixtures --perf && PERF=1 npx playwright test perf.spec.ts   # perf check
```

All fixtures are synthetic and generated in code (`scripts/make_fixtures.py`); no patient data is committed.

## Data root and clearing data

Everything (SQLite database and case files) lives under `MSLV_DATA_ROOT` (default `./data`, gitignored).

```bash
MSLV_DATA_ROOT=/path/to/data scripts/start.sh      # choose a location
rm -rf data                                         # clear ALL cases (stop the app first)
```

Deleting a case in the UI removes its files, masks, measurements and jobs. This is ordinary file deletion, not forensic secure erasure.

## Docker (CPU demo)

```bash
docker compose up --build        # http://127.0.0.1:8000, data persisted in ./data
```

Note: the Docker files were written but could not be run on the development machine (no Docker installed). Treat them as untested.

## Using the app

| | |
|---|---|
| Upload | One 3D `.nii` / `.nii.gz`. Alias, sequence label, patient alias, timepoint and scan date are optional user labels. |
| Mask | Binary (0/1) NIfTI on the **same grid** as the MRI (same shape; affine within 0.01 mm). Otherwise it is rejected with an explanation. |
| Navigate | Wheel or ↑/↓ over a plane; click/drag in a plane to move the 3D cursor; click the lesion strip to jump. |
| View | Ctrl/⌘+wheel zoom, Shift+drag pan, **M** mask, **D** diameters, **R** reset. |
| Lesions | Click a row to jump to the lesion; click again to deselect. |

Data handling: uploads are **not** anonymised by this app - use synthetic or de-identified data. There is no authentication;
the server binds to 127.0.0.1 and must not be exposed remotely without a separate security design.

### Using the open_ms_data dataset

`cross_sectional/coregistered/patientXX/FLAIR.nii.gz` with `consensus_gt.nii.gz` loads directly, with one note: the dataset's
mask headers are offset from the FLAIR by exactly half a voxel. The app rejects that by default (it cannot know which header is right)
and offers **Accept sub-voxel header offset and retry**; the choice is recorded in the provenance JSON. In
`longitudinal/coregistered/` the `gt.nii.gz` is a lesion *change* mask (not a per-scan mask).

## Connecting a real segmentation adapter

See [docs/REAL_ADAPTER.md](docs/REAL_ADAPTER.md). In short: install your versioned inference package into the backend environment,
then

```bash
MSLV_ADAPTER=my_package.adapter:create_adapter MSLV_ADAPTER_CONFIG='{"checkpoint":"/models/v1.pt"}' MSLV_DEVICE=cuda:0 scripts/start.sh
```

The app never downloads weights, imports training code, or loads models from the upload UI.

## More

* [SPEC.md](SPEC.md) - revised specification, decisions and deviations
* [docs/VERIFICATION.md](docs/VERIFICATION.md) - acceptance results, measured performance, known limitations
* [docs/FRONTEND.md](docs/FRONTEND.md) - map of the TypeScript code
* API docs: with the server running, <http://127.0.0.1:8000/docs> (OpenAPI at `/openapi.json`)
