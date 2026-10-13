"""Orientation / alignment tests against the world-space phantom (independent of the renderer)."""
import numpy as np
import pytest

from app.imaging import geometry, phantom
from app.imaging.geometry import extract_raster, make_display_plan, apply_plan
from app.imaging.nifti import max_corner_distance

SHAPE = (56, 64, 48)
SP = (2.0, 2.0, 2.5)

P = np.eye(3)
DIRECTIONS = {
    "RAS": np.eye(3),
    "LPS": np.diag([-1.0, -1.0, 1.0]),
    "flip_z": np.diag([1.0, 1.0, -1.0]),
    "permuted": np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], float),
    "permuted_neg": np.array([[0, -1, 0], [1, 0, 0], [0, 0, -1]], float),
}
oblique = np.array([[np.cos(0.35), -np.sin(0.35), 0], [np.sin(0.35), np.cos(0.35), 0], [0, 0, 1]])
oblique = oblique @ np.array([[1, 0, 0], [0, np.cos(0.25), -np.sin(0.25)], [0, np.sin(0.25), np.cos(0.25)]])
DIRECTIONS["oblique"] = oblique


def build(direction):
    shape = SHAPE
    aff = phantom.centred_affine(shape, SP, direction)
    aff[:3, 3] += np.array([13.0, -7.0, 4.0])      # translation: world origin is not at the centre
    vol, mask = phantom.make_phantom(shape, aff)
    plan = make_display_plan(shape, aff)
    return aff, vol, mask, plan, apply_plan(plan, vol, 1), apply_plan(plan, mask, 0, np.uint8)


def landmark_raster_pos(plan, disp, plane, name):
    """Find the landmark in the raster by intensity (independent of coordinate maths under test)."""
    c, r, val = phantom.LANDMARKS[name]
    idx = np.rint((np.array(c) - plan.affine[:3, 3]) / np.array(plan.spacing)).astype(int)
    a, b, s = geometry.PLANE_AXES[plane]
    sl = int(idx[s])
    raster = extract_raster(disp, plane, sl)
    ys, xs = np.nonzero(np.abs(raster - val) < 1.0)
    assert xs.size, f"{name} not visible in {plane}"
    return xs.mean(), ys.mean(), raster.shape[1], raster.shape[0]


@pytest.mark.parametrize("name", list(DIRECTIONS))
def test_display_grid_matches_world_object(name):
    """Display volume equals the phantom evaluated directly on the display grid (no mirrored anatomy)."""
    aff, vol, mask, plan, disp, dmask = build(DIRECTIONS[name])
    assert np.allclose(plan.affine[:3, :3], np.diag(plan.spacing))      # RAS+, axis aligned, positive
    ref, ref_mask = phantom.make_phantom(plan.shape, plan.affine)
    if not plan.resampled:
        assert plan.method == "orientation_permutation"
        assert np.array_equal(disp, ref)                                  # exact, no interpolation
        assert np.array_equal(dmask, ref_mask)
    else:
        assert plan.resampled and len(set(np.round(plan.spacing, 6))) == 1
        inter = (dmask > 0) & (ref_mask > 0)
        dice = 2 * inter.sum() / ((dmask > 0).sum() + (ref_mask > 0).sum())
        assert dice > 0.7
        nz = (ref > 0) & (disp > 0)
        assert np.corrcoef(ref[nz], disp[nz])[0, 1] > 0.9


@pytest.mark.parametrize("name", list(DIRECTIONS))
def test_radiological_orientation_of_each_plane(name):
    _, _, _, plan, disp, _ = build(DIRECTIONS[name])
    # axial: left = patient Right (+x), top = Anterior (+y)
    x, y, w, h = landmark_raster_pos(plan, disp, "axial", "right");   assert x < w / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "axial", "left");    assert x > w / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "axial", "anterior");  assert y < h / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "axial", "posterior"); assert y > h / 2
    # coronal: left = Right, top = Superior
    x, y, w, h = landmark_raster_pos(plan, disp, "coronal", "right");    assert x < w / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "coronal", "superior"); assert y < h / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "coronal", "inferior"); assert y > h / 2
    # sagittal: left = Anterior, top = Superior
    x, y, w, h = landmark_raster_pos(plan, disp, "sagittal", "anterior");  assert x < w / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "sagittal", "posterior"); assert x > w / 2
    x, y, w, h = landmark_raster_pos(plan, disp, "sagittal", "superior");  assert y < h / 2


def test_raster_position_matches_physical_coordinates():
    """Pixel position of a landmark, converted with the raster contract, lands on its world position."""
    _, _, _, plan, disp, _ = build(DIRECTIONS["LPS"])
    for name in ("right", "anterior", "superior"):
        c, _, val = phantom.LANDMARKS[name]
        for plane in geometry.PLANES:
            a, b, s = geometry.PLANE_AXES[plane]
            idx = np.rint((np.array(c) - plan.affine[:3, 3]) / np.array(plan.spacing)).astype(int)
            raster = extract_raster(disp, plane, int(idx[s]))
            ys, xs = np.nonzero(np.abs(raster - val) < 1.0)
            ras = geometry.raster_to_ras(plan, plane, int(idx[s]), xs.mean(), ys.mean())
            assert np.allclose(ras[a], c[a], atol=1.0) and np.allclose(ras[b], c[b], atol=1.0)
            assert abs(ras[s] - c[s]) <= plan.spacing[s] / 2 + 1e-6


def test_anisotropic_plane_geometry_and_labels():
    _, _, _, plan, _, _ = build(np.eye(3))
    g = geometry.plane_geometry(plan, "coronal")
    assert g["pixel_spacing_mm"] == [2.0, 2.5] and g["slice_axis"] == "y"
    assert geometry.plane_geometry(plan, "axial")["edge_labels"] == {"left": "R", "right": "L", "top": "A", "bottom": "P"}
    assert geometry.plane_geometry(plan, "sagittal")["edge_labels"] == {"left": "A", "right": "P", "top": "S", "bottom": "I"}


def test_oblique_uses_isotropic_min_spacing_and_covers_bounds():
    aff, _, _, plan, _, _ = build(DIRECTIONS["oblique"])
    assert plan.resampled and np.allclose(plan.spacing, min(SP))
    from app.imaging.nifti import voxel_corners
    c = voxel_corners(SHAPE, aff)
    hi = plan.affine[:3, 3] + (np.array(plan.shape) - 1) * np.array(plan.spacing)
    assert (c.min(0) >= plan.affine[:3, 3] - 1e-6).all() and (c.max(0) <= hi + 1e-6).all()


def test_corner_distance_detects_shift():
    aff = phantom.centred_affine(SHAPE, SP)
    shifted = aff.copy(); shifted[0, 3] += 1.0
    assert max_corner_distance(SHAPE, aff, SHAPE, aff) == 0
    assert max_corner_distance(SHAPE, aff, SHAPE, shifted) == pytest.approx(1.0)
