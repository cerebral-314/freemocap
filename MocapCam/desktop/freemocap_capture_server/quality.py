from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaptureQuality:
    score: int
    warnings: list[str]


def score_device_status(status: dict[str, Any], sync_report: dict[str, Any] | None = None) -> CaptureQuality:
    score = 100
    warnings: list[str] = []

    fps = float(status.get("fps") or 0)
    if fps < 20:
        score -= 20
        warnings.append(f"low RGB FPS ({fps:.1f})")

    if status.get("depth_supported") and float(status.get("depth_frames_sent") or 0) <= 0:
        score -= 10
        warnings.append("depth supported but no depth frames observed")

    battery = status.get("battery_percent")
    if battery is not None and int(battery) < 25:
        score -= 10
        warnings.append(f"low battery ({battery}%)")

    thermal = str(status.get("thermal_state") or "").lower()
    if thermal in {"serious", "critical"}:
        score -= 25
        warnings.append(f"thermal state is {thermal}")

    dropped = int(status.get("dropped_frames") or 0) + int(status.get("dropped_depth_frames") or 0)
    if dropped:
        score -= min(20, dropped)
        warnings.append(f"{dropped} dropped local frames")

    if sync_report:
        latency = float(sync_report.get("median_round_trip_latency_ms") or 0)
        if latency > 25:
            score -= 10
            warnings.append(f"high clock-sync RTT ({latency:.1f} ms)")
        if int(sync_report.get("sample_count") or 0) < 3:
            score -= 15
            warnings.append("too few clock-sync samples")

    return CaptureQuality(score=max(score, 0), warnings=warnings)


def score_session(device_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    device_scores = {
        device_id: score_device_status(report.get("status", {}), report.get("sync")).__dict__
        for device_id, report in device_reports.items()
    }
    overall = min((score["score"] for score in device_scores.values()), default=0)
    return {"overall_score": overall, "devices": device_scores}
