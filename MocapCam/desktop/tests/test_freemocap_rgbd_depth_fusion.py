import importlib.util
import sys
from pathlib import Path

import numpy as np


MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "freemocap"
    / "core_processes"
    / "capture_volume_calibration"
    / "rgbd_depth_fusion.py"
)
spec = importlib.util.spec_from_file_location("rgbd_depth_fusion", MODULE_PATH)
rgbd_depth_fusion = importlib.util.module_from_spec(spec)
assert spec is not None
assert spec.loader is not None
sys.modules[spec.name] = rgbd_depth_fusion
spec.loader.exec_module(rgbd_depth_fusion)

DepthFusionSettings = rgbd_depth_fusion.DepthFusionSettings
build_depth_observations = rgbd_depth_fusion.build_depth_observations
refine_3d_with_depth = rgbd_depth_fusion.refine_3d_with_depth
save_depth_observations = rgbd_depth_fusion.save_depth_observations
load_depth_observations = rgbd_depth_fusion.load_depth_observations


def test_build_depth_observations_from_maps():
    image_2d = np.array([[[[1.0, 1.0]]]])
    depth_maps = np.ones((1, 1, 3, 3), dtype=float)
    intrinsics = np.array([[[1.0, 0.0, 1.0], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0]]])
    camera_to_world = np.array([np.eye(4)])

    points, valid = build_depth_observations(
        image_2d,
        depth_maps,
        intrinsics,
        camera_to_world,
        patch_radius_px=1,
        min_valid_depth_pixels=1,
    )

    np.testing.assert_allclose(points, [[[0.0, 0.0, 1.0]]])
    assert valid.tolist() == [[True]]


def test_refine_3d_with_depth_weighted_average():
    baseline = np.array([[[0.0, 0.0, 1.0]]])
    depth = np.array([[[0.0, 0.0, 1.2]]])
    valid = np.array([[True]])

    refined, diagnostics = refine_3d_with_depth(
        baseline,
        depth,
        valid,
        DepthFusionSettings(depth_weight=1.0, max_depth_joint_distance_m=0.5),
    )

    np.testing.assert_allclose(refined, [[[0.0, 0.0, 1.1]]])
    assert diagnostics["accepted_depth_points"] == 1


def test_depth_observation_round_trip(tmp_path):
    points = np.array([[[1.0, 2.0, 3.0]]])
    valid = np.array([[True]])
    save_depth_observations(tmp_path, points, valid)
    loaded_points, loaded_valid = load_depth_observations(tmp_path)
    np.testing.assert_allclose(loaded_points, points)
    assert loaded_valid.tolist() == [[True]]
