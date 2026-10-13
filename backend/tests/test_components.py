import itertools

import numpy as np
import pytest
from scipy.spatial.distance import pdist

from app.imaging import components, geometry
from app.imaging.components import feret_2d, feret_3d, label_native, slice_footprints
from app.imaging.geometry import make_display_plan


def brute_feret(rows, cols, sc, sr):
    pts = [((c + dc) * sc, (r + dr) * sr) for r, c in zip(rows, cols) for dr in (-.5, .5) for dc in (-.5, .5)]
    return pdist(np.array(pts)).max()


@pytest.mark.parametrize("sc,sr", [(1.0, 1.0), (0.5, 2.0), (0.72, 3.0)])
def test_single_pixel_is_cell_diagonal(sc, sr):
    d, _, _ = feret_2d(np.array([5]), np.array([7]), sc, sr)
    assert d == pytest.approx(np.hypot(sc, sr), abs=1e-9)


@pytest.mark.parametrize("h,w,sc,sr", [(3, 7, 1.0, 1.0), (4, 2, 0.5, 2.0), (1, 9, 0.72, 3.0)])
def test_rectangle_feret_is_diagonal(h, w, sc, sr):
    r, c = np.mgrid[2:2 + h, 3:3 + w]
    d, p0, p1 = feret_2d(r.ravel(), c.ravel(), sc, sr)
    assert d == pytest.approx(np.hypot(w * sc, h * sr), abs=1e-9)
    assert np.hypot((p0[0] - p1[0]) * sc, (p0[1] - p1[1]) * sr) == pytest.approx(d)


def test_random_shapes_match_bruteforce():
    rng = np.random.default_rng(0)
    for _ in range(20):
        m = rng.random((15, 15)) > 0.6
        r, c = np.nonzero(m)
        if r.size:
            d, _, _ = feret_2d(r, c, 0.7, 2.2)
            assert d == pytest.approx(brute_feret(r, c, 0.7, 2.2), abs=1e-9)


def test_feret_3d_box_and_anisotropy():
    idx = np.array(list(itertools.product(range(2, 6), range(1, 4), range(0, 7))))   # 4 x 3 x 7 voxels
    aff = np.diag([0.5, 1.0, 3.0, 1.0])
    d, _, _ = feret_3d(idx, aff)
    assert d == pytest.approx(np.sqrt((4 * .5) ** 2 + (3 * 1.0) ** 2 + (7 * 3.0) ** 2), abs=1e-9)
    d1, _, _ = feret_3d(np.array([[1, 1, 1]]), aff)
    assert d1 == pytest.approx(np.sqrt(.25 + 1 + 9))


def test_candidate_ids_are_stable_and_ordered():
    m = np.zeros((10, 10, 10), np.uint8)
    m[7, 1, 1] = 1                 # later in x
    m[2, 8, 8] = 1                 # first in lexicographic [x,y,z]
    m[2, 2, 2] = 1                 # touches nothing; x=2,y=2 -> before (2,8,8)
    m[8, 1, 2] = 1                 # diagonal neighbour of (7,1,1): 26-connected -> same candidate
    lab, n = label_native(m)
    assert n == 3
    assert lab[2, 2, 2] == 1 and lab[2, 8, 8] == 2 and lab[7, 1, 1] == 3 == lab[8, 1, 2]
    lab2, _ = label_native(m)
    assert np.array_equal(lab, lab2)


def test_single_voxel_components_are_kept_and_empty_mask_ok():
    m = np.zeros((5, 5, 5), np.uint8); m[1, 1, 1] = 1; m[3, 3, 3] = 1
    assert label_native(m)[1] == 2
    assert label_native(np.zeros((5, 5, 5), np.uint8))[1] == 0


def test_two_footprints_of_one_candidate_on_a_slice_share_the_id():
    lab = np.zeros((20, 12, 8), np.int32)       # a U shape: two arms in z=4, joined at z=2 (same 3D candidate)
    lab[3:6, 3:6, 2:5] = 1
    lab[12:15, 3:6, 2:5] = 1
    lab[3:15, 3:6, 2] = 1
    plan = make_display_plan(lab.shape, np.eye(4))
    r = geometry.extract_raster(lab, "axial", 4)
    fps = slice_footprints(r, plan, "axial", 4, (1.0, 1.0))
    assert [f["candidate_id"] for f in fps] == [1, 1] and [f["footprint_id"] for f in fps] == [1, 2]
    for f in fps:
        assert f["diameter_mm"] == pytest.approx(np.hypot(3, 3))


def test_footprint_endpoints_in_ras_follow_the_display_affine():
    lab = np.zeros((10, 10, 5), np.int32); lab[2:5, 3:8, 2] = 1
    aff = np.diag([2.0, 1.0, 3.0, 1.0]); aff[:3, 3] = [10, 20, 30]
    plan = make_display_plan(lab.shape, aff)
    r = geometry.extract_raster(lab, "axial", 2)
    f = slice_footprints(r, plan, "axial", 2, (2.0, 1.0))[0]
    p0, p1 = (np.array(e) for e in f["endpoints_ras_mm"])
    assert np.linalg.norm(p0 - p1) == pytest.approx(f["diameter_mm"], abs=1e-9)
    assert p0[2] == pytest.approx(30 + 2 * 3.0) == p1[2]
    # footprint spans 3 px in x (2 mm each) and 5 px in y (1 mm each)
    assert f["diameter_mm"] == pytest.approx(np.hypot(3 * 2.0, 5 * 1.0))
