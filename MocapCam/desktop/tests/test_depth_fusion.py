import numpy as np

from freemocap_plugin.depth_fusion import (
    DepthFusionParameters,
    fuse_triangulated_with_depth,
    sample_depth_patch,
    unproject_depth_to_camera,
)


def test_sample_depth_patch_uses_valid_median():
    depth = np.zeros((5, 5), dtype=float)
    depth[1:4, 1:4] = np.arange(1, 10).reshape(3, 3)
    assert sample_depth_patch(depth, 2, 2, radius_px=1, min_valid_depth_pixels=5) == 5.0


def test_unproject_depth_to_camera():
    intrinsics = np.array([[100.0, 0.0, 50.0], [0.0, 100.0, 60.0], [0.0, 0.0, 1.0]])
    point = unproject_depth_to_camera(60.0, 80.0, 2.0, intrinsics)
    np.testing.assert_allclose(point, [0.2, 0.4, 2.0])


def test_fuse_rejects_far_depth_outlier():
    baseline = np.array([[[1.0, 1.0, 1.0]]])
    depth = np.array([[[10.0, 10.0, 10.0]]])
    valid = np.array([[True]])
    fused = fuse_triangulated_with_depth(
        baseline,
        depth,
        valid,
        DepthFusionParameters(max_depth_joint_distance_m=0.25),
    )
    np.testing.assert_allclose(fused, baseline)
