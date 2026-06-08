from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BodyOptimizationParameters:
    temporal_smoothing_alpha: float = 0.35
    max_joint_velocity_m_per_frame: float = 1.0
    ground_height_m: float = 0.0
    foot_marker_indices: tuple[int, ...] = ()
    foot_lock_velocity_threshold_m_per_frame: float = 0.015


def temporal_smooth(skeleton_frame_marker_xyz: np.ndarray, alpha: float = 0.35) -> np.ndarray:
    if skeleton_frame_marker_xyz.shape[0] < 2:
        return skeleton_frame_marker_xyz.copy()
    smoothed = skeleton_frame_marker_xyz.copy().astype(float)
    for frame_index in range(1, smoothed.shape[0]):
        previous = smoothed[frame_index - 1]
        current = smoothed[frame_index]
        valid = np.isfinite(previous).all(axis=-1) & np.isfinite(current).all(axis=-1)
        smoothed[frame_index, valid] = alpha * current[valid] + (1.0 - alpha) * previous[valid]
    return smoothed


def limit_joint_velocity(skeleton_frame_marker_xyz: np.ndarray, max_velocity_m_per_frame: float) -> np.ndarray:
    limited = skeleton_frame_marker_xyz.copy().astype(float)
    for frame_index in range(1, limited.shape[0]):
        delta = limited[frame_index] - limited[frame_index - 1]
        distances = np.linalg.norm(delta, axis=-1)
        too_fast = np.isfinite(distances) & (distances > max_velocity_m_per_frame)
        if np.any(too_fast):
            scale = max_velocity_m_per_frame / distances[too_fast]
            limited[frame_index, too_fast] = limited[frame_index - 1, too_fast] + delta[too_fast] * scale[:, None]
    return limited


def apply_ground_contact(
    skeleton_frame_marker_xyz: np.ndarray,
    foot_marker_indices: tuple[int, ...],
    ground_height_m: float = 0.0,
    velocity_threshold_m_per_frame: float = 0.015,
) -> np.ndarray:
    adjusted = skeleton_frame_marker_xyz.copy().astype(float)
    if not foot_marker_indices:
        return adjusted

    adjusted[:, foot_marker_indices, 2] = np.maximum(adjusted[:, foot_marker_indices, 2], ground_height_m)
    for marker_index in foot_marker_indices:
        for frame_index in range(1, adjusted.shape[0]):
            previous = adjusted[frame_index - 1, marker_index]
            current = adjusted[frame_index, marker_index]
            if not np.isfinite(previous).all() or not np.isfinite(current).all():
                continue
            velocity = np.linalg.norm(current - previous)
            if velocity <= velocity_threshold_m_per_frame and abs(current[2] - ground_height_m) <= 0.03:
                adjusted[frame_index, marker_index, 2] = ground_height_m
    return adjusted


def optimize_body_trajectory(
    skeleton_frame_marker_xyz: np.ndarray,
    parameters: BodyOptimizationParameters | None = None,
) -> np.ndarray:
    parameters = parameters or BodyOptimizationParameters()
    optimized = temporal_smooth(skeleton_frame_marker_xyz, parameters.temporal_smoothing_alpha)
    optimized = limit_joint_velocity(optimized, parameters.max_joint_velocity_m_per_frame)
    optimized = apply_ground_contact(
        optimized,
        foot_marker_indices=parameters.foot_marker_indices,
        ground_height_m=parameters.ground_height_m,
        velocity_threshold_m_per_frame=parameters.foot_lock_velocity_threshold_m_per_frame,
    )
    return optimized
