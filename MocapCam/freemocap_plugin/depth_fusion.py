from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DepthFusionParameters:
    use_depth_fusion: bool = True
    depth_weight: float = 1.0
    max_depth_joint_distance_m: float = 0.25
    depth_patch_radius_px: int = 3
    min_valid_depth_pixels: int = 5
    reject_depth_edges: bool = True
    use_depth_for_occlusion_reasoning: bool = True
    save_rgbd_diagnostics: bool = True


def sample_depth_patch(
    depth_meters: np.ndarray,
    u: float,
    v: float,
    radius_px: int = 3,
    min_valid_depth_pixels: int = 5,
) -> float | None:
    if depth_meters.ndim != 2 or not np.isfinite([u, v]).all():
        return None

    height, width = depth_meters.shape
    center_x = int(round(u))
    center_y = int(round(v))
    x0 = max(0, center_x - radius_px)
    x1 = min(width, center_x + radius_px + 1)
    y0 = max(0, center_y - radius_px)
    y1 = min(height, center_y + radius_px + 1)
    if x0 >= x1 or y0 >= y1:
        return None

    patch = depth_meters[y0:y1, x0:x1]
    valid = patch[np.isfinite(patch) & (patch > 0)]
    if valid.size < min_valid_depth_pixels:
        return None
    return float(np.median(valid))


def unproject_depth_to_camera(u: float, v: float, z_meters: float, intrinsics: np.ndarray) -> np.ndarray:
    fx = intrinsics[0, 0]
    fy = intrinsics[1, 1]
    cx = intrinsics[0, 2]
    cy = intrinsics[1, 2]
    return np.array([(u - cx) / fx * z_meters, (v - cy) / fy * z_meters, z_meters], dtype=float)


def transform_camera_to_world(point_camera: np.ndarray, camera_to_world: np.ndarray) -> np.ndarray:
    homogeneous = np.ones(4, dtype=float)
    homogeneous[:3] = point_camera
    return (camera_to_world @ homogeneous)[:3]


def fuse_triangulated_with_depth(
    triangulated_frame_marker_xyz: np.ndarray,
    depth_observations_frame_marker_xyz: np.ndarray,
    depth_valid_frame_marker: np.ndarray,
    parameters: DepthFusionParameters | None = None,
) -> np.ndarray:
    parameters = parameters or DepthFusionParameters()
    if not parameters.use_depth_fusion:
        return triangulated_frame_marker_xyz.copy()

    fused = triangulated_frame_marker_xyz.copy().astype(float)
    for frame_index in range(fused.shape[0]):
        for marker_index in range(fused.shape[1]):
            if not depth_valid_frame_marker[frame_index, marker_index]:
                continue
            baseline = fused[frame_index, marker_index]
            depth_point = depth_observations_frame_marker_xyz[frame_index, marker_index]
            if not np.isfinite(depth_point).all():
                continue
            if np.isfinite(baseline).all():
                distance = float(np.linalg.norm(depth_point - baseline))
                if distance > parameters.max_depth_joint_distance_m:
                    continue
                fused[frame_index, marker_index] = (
                    baseline + parameters.depth_weight * depth_point
                ) / (1.0 + parameters.depth_weight)
            else:
                fused[frame_index, marker_index] = depth_point
    return fused
