from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class ClockSyncSample:
    request_id: str | None
    server_time_send_ns: int
    device_time_receive_ns: int
    device_time_reply_ns: int
    server_time_receive_ns: int

    @property
    def round_trip_latency_ms(self) -> float:
        device_turnaround_ns = self.device_time_reply_ns - self.device_time_receive_ns
        network_ns = (self.server_time_receive_ns - self.server_time_send_ns) - device_turnaround_ns
        return max(network_ns, 0) / 1_000_000

    @property
    def offset_ns(self) -> float:
        server_midpoint = (self.server_time_send_ns + self.server_time_receive_ns) / 2
        device_midpoint = (self.device_time_receive_ns + self.device_time_reply_ns) / 2
        return device_midpoint - server_midpoint


class ClockSyncEstimator:
    def __init__(self) -> None:
        self._samples: list[ClockSyncSample] = []

    @property
    def samples(self) -> list[ClockSyncSample]:
        return list(self._samples)

    def add(self, sample: ClockSyncSample) -> None:
        self._samples.append(sample)

    def offset_ns(self) -> float:
        if not self._samples:
            return 0.0
        best_samples = sorted(self._samples, key=lambda sample: sample.round_trip_latency_ms)
        keep = max(1, len(best_samples) // 2)
        return median(sample.offset_ns for sample in best_samples[:keep])

    def drift_ppm(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        first = self._samples[0]
        last = self._samples[-1]
        server_elapsed = last.server_time_receive_ns - first.server_time_receive_ns
        if server_elapsed <= 0:
            return 0.0
        offset_delta = last.offset_ns - first.offset_ns
        return (offset_delta / server_elapsed) * 1_000_000

    def report(self) -> dict[str, float | int]:
        return {
            "sample_count": len(self._samples),
            "estimated_offset_ms": self.offset_ns() / 1_000_000,
            "estimated_drift_ppm": self.drift_ppm(),
            "median_round_trip_latency_ms": median([sample.round_trip_latency_ms for sample in self._samples])
            if self._samples
            else 0.0,
        }
