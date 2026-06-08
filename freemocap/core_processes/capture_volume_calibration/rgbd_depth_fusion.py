from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class DepthFusionSettings:
    use_depth_fusion: bool = True
    depth_weight: float = 1.0
    max_depth_joint_distance_m: float = 0.25


def load_depth_observations(raw_data_folder_path: str | Path) -> tuple[np.ndarray, np.ndarray] | None:
    observation_path = Path(raw_data_folder_path) / "rgbd_depth_observations.npz"
    if not observation_path.exists():
        return None

    observation_file = np.load(observation_path)
    if "depth_points_xyz" not in observation_file or "depth_valid_mask" not in observation_file:
        raise ValueError(
            f"{observation_path} must contain 'depth_points_xyz' and 'depth_valid_mask' arrays"
        )
    return observation_file["depth_points_xyz"], observation_file["depth_valid_mask"].astype(bool)


def save_depth_observations(
    raw_data_folder_path: str | Path,
    depth_points_frame_marker_xyz: np.ndarray,
    depth_valid_frame_marker: np.ndarray,
) -> Path:
    output_path = Path(raw_data_folder_path) / "rgbd_depth_observations.npz"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        depth_points_xyz=depth_points_frame_marker_xyz,
        depth_valid_mask=depth_valid_frame_marker.astype(bool),
    )
    return output_path


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


def transform_camera_to_world(point_camera_xyz: np.ndarray, camera_to_world: np.ndarray) -> np.ndarray:
    point_homogeneous = np.ones(4, dtype=float)
    point_homogeneous[:3] = point_camera_xyz
    return (camera_to_world @ point_homogeneous)[:3]


def build_depth_observations(
    image_2d_data_num_cams_num_frames_num_markers_xy: np.ndarray,
    depth_maps_num_cams_num_frames_height_width: np.ndarray,
    intrinsics_num_cams_3x3: np.ndarray,
    camera_to_world_num_cams_4x4: np.ndarray,
    patch_radius_px: int = 3,
    min_valid_depth_pixels: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    num_cams, num_frames, num_markers, _ = image_2d_data_num_cams_num_frames_num_markers_xy.shape
    depth_points = np.full((num_frames, num_markers, 3), np.nan, dtype=float)
    depth_valid = np.zeros((num_frames, num_markers), dtype=bool)

    for frame_index in range(num_frames):
        for marker_index in range(num_markers):
            observations = []
            for camera_index in range(num_cams):
                u, v = image_2d_data_num_cams_num_frames_num_markers_xy[camera_index, frame_index, marker_index, :2]
                z_meters = sample_depth_patch(
                    depth_maps_num_cams_num_frames_height_width[camera_index, frame_index],
                    float(u),
                    float(v),
                    radius_px=patch_radius_px,
                    min_valid_depth_pixels=min_valid_depth_pixels,
                )
                if z_meters is None:
                    continue
                point_camera = unproject_depth_to_camera(
                    float(u),
                    float(v),
                    z_meters,
                    intrinsics_num_cams_3x3[camera_index],
                )
                observations.append(transform_camera_to_world(point_camera, camera_to_world_num_cams_4x4[camera_index]))
            if observations:
                depth_points[frame_index, marker_index] = np.median(np.asarray(observations), axis=0)
                depth_valid[frame_index, marker_index] = True

    return depth_points, depth_valid


def refine_3d_with_depth(
    triangulated_frame_marker_xyz: np.ndarray,
    depth_points_frame_marker_xyz: np.ndarray,
    depth_valid_frame_marker: np.ndarray,
    settings: DepthFusionSettings,
) -> tuple[np.ndarray, dict[str, int]]:
    if not settings.use_depth_fusion:
        return triangulated_frame_marker_xyz.copy(), {"accepted_depth_points": 0, "rejected_depth_points": 0}

    if triangulated_frame_marker_xyz.shape != depth_points_frame_marker_xyz.shape:
        raise ValueError(
            "Depth observation shape must match triangulated data shape: "
            f"{depth_points_frame_marker_xyz.shape} != {triangulated_frame_marker_xyz.shape}"
        )
    if depth_valid_frame_marker.shape != triangulated_frame_marker_xyz.shape[:2]:
        raise ValueError(
            "Depth validity mask must have shape frames x tracked_points: "
            f"{depth_valid_frame_marker.shape} != {triangulated_frame_marker_xyz.shape[:2]}"
        )

    refined = triangulated_frame_marker_xyz.copy().astype(float)
    accepted = 0
    rejected = 0
    for frame_index in range(refined.shape[0]):
        for marker_index in range(refined.shape[1]):
            if not depth_valid_frame_marker[frame_index, marker_index]:
                continue

            baseline = refined[frame_index, marker_index]
            depth_point = depth_points_frame_marker_xyz[frame_index, marker_index]
            if not np.isfinite(depth_point).all():
                rejected += 1
                continue

            if np.isfinite(baseline).all():
                distance = float(np.linalg.norm(depth_point - baseline))
                if distance > settings.max_depth_joint_distance_m:
                    rejected += 1
                    continue
                refined[frame_index, marker_index] = (
                    baseline + settings.depth_weight * depth_point
                ) / (1.0 + settings.depth_weight)
            else:
                refined[frame_index, marker_index] = depth_point
            accepted += 1

    return refined, {"accepted_depth_points": accepted, "rejected_depth_points": rejected}
