"""Synthetic asymmetric phantom, defined in WORLD (RAS mm) space.

Because the object is a function of world coordinates, sampling it under any affine (flipped,
permuted, anisotropic, oblique) yields the same physical object. Tests and the demo rely on this
to verify orientation independently of the renderer."""
from __future__ import annotations

import numpy as np

# name -> (centre RAS mm, radii mm, intensity); distinct intensity and size per landmark.
LANDMARKS = {
    "right": ((40.0, 0.0, 0.0), (7.0, 7.0, 7.0), 400.0),
    "left": ((-40.0, 0.0, 0.0), (5.0, 5.0, 5.0), 250.0),
    "anterior": ((0.0, 50.0, 0.0), (6.0, 6.0, 6.0), 600.0),
    "posterior": ((0.0, -50.0, 0.0), (4.5, 4.5, 4.5), 500.0),
    "superior": ((0.0, 0.0, 35.0), (8.0, 8.0, 8.0), 700.0),
    "inferior": ((0.0, 0.0, -35.0), (4.5, 4.5, 4.5), 800.0),
}
HEAD_RADII = (70.0, 90.0, 60.0)
# Lesions (centre, radii) in world mm. The first two touch (one shared 3D candidate); one is tiny.
LESIONS = [
    ((22.0, 20.0, 10.0), (6.0, 4.0, 3.0)),
    ((28.0, 20.0, 10.0), (4.0, 3.0, 3.0)),
    ((-25.0, -15.0, 18.0), (3.0, 3.0, 2.0)),
    ((-10.0, 30.0, -12.0), (1.6, 1.6, 1.6)),
    ((5.0, -30.0, 22.0), (8.0, 2.5, 2.5)),
]


def world_coords(shape, affine):
    i, j, k = (np.arange(n, dtype=np.float64) for n in shape)
    a = np.asarray(affine, dtype=np.float64)
    out = []
    for r in range(3):
        out.append((a[r, 0] * i[:, None, None] + a[r, 1] * j[None, :, None] + a[r, 2] * k[None, None, :] + a[r, 3]).astype(np.float32))
    return tuple(out)


def _ell(X, Y, Z, c, r) -> np.ndarray:
    return ((X - c[0]) / r[0]) ** 2 + ((Y - c[1]) / r[1]) ** 2 + ((Z - c[2]) / r[2]) ** 2 <= 1.0


def make_phantom(shape, affine, lesions=None):
    """Return (mri float32, lesion mask uint8) sampled on the given voxel grid."""
    X, Y, Z = world_coords(shape, affine)
    vol = np.zeros(shape, dtype=np.float32)
    vol[_ell(X, Y, Z, (0, 0, 0), HEAD_RADII)] = 60.0
    brain = _ell(X, Y, Z, (0, 0, 0), (62.0, 80.0, 52.0))
    # smooth deterministic texture (a function of world position only) so intensity percentiles are realistic
    vol[brain] = (150.0 + 30.0 * np.sin(X / 9.0) * np.cos(Y / 11.0) + 15.0 * np.sin(Z / 7.0))[brain]
    vol[_ell(X, Y, Z, (0, 5, 5), (10.0, 20.0, 8.0))] = 40.0
    for c, r, val in LANDMARKS.values():
        vol[_ell(X, Y, Z, c, r)] = val
    mask = np.zeros(shape, dtype=np.uint8)
    for c, r in (LESIONS if lesions is None else lesions):
        mask[_ell(X, Y, Z, c, r)] = 1
    vol[mask > 0] = np.maximum(vol[mask > 0], 330.0)
    return vol, mask


def centred_affine(shape, spacing, direction=None) -> np.ndarray:
    """Affine placing the world origin at the volume centre; direction is an optional 3x3 axis matrix."""
    d = np.eye(3) if direction is None else np.asarray(direction, float)
    lin = d @ np.diag(spacing)
    a = np.eye(4)
    a[:3, :3] = lin
    a[:3, 3] = -(lin @ ((np.asarray(shape, float) - 1) / 2.0))
    return a
