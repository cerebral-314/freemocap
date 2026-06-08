import numpy as np

from freemocap_plugin.optimization import (
    BodyOptimizationParameters,
    apply_ground_contact,
    limit_joint_velocity,
    optimize_body_trajectory,
)


def test_limit_joint_velocity_clamps_large_jump():
    skeleton = np.array([[[0.0, 0.0, 0.0]], [[10.0, 0.0, 0.0]]])
    limited = limit_joint_velocity(skeleton, max_velocity_m_per_frame=1.0)
    np.testing.assert_allclose(limited[1, 0], [1.0, 0.0, 0.0])


def test_apply_ground_contact_clamps_foot_below_floor():
    skeleton = np.array([[[0.0, 0.0, -0.1]]])
    adjusted = apply_ground_contact(skeleton, foot_marker_indices=(0,), ground_height_m=0.0)
    assert adjusted[0, 0, 2] == 0.0


def test_optimize_body_trajectory_runs_pipeline():
    skeleton = np.array([[[0.0, 0.0, -0.1]], [[3.0, 0.0, -0.1]]])
    optimized = optimize_body_trajectory(
        skeleton,
        BodyOptimizationParameters(max_joint_velocity_m_per_frame=1.0, foot_marker_indices=(0,)),
    )
    assert optimized.shape == skeleton.shape
    assert np.all(optimized[:, 0, 2] >= 0)
