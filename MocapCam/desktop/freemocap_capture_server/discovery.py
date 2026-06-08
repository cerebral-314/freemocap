from __future__ import annotations

import time
from dataclasses import dataclass

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeroconf import IPVersion, ServiceInfo

SERVICE_PROTOCOL = "_mocapcam._tcp"
SERVICE_TYPE = f"{SERVICE_PROTOCOL}.local."


@dataclass(frozen=True)
class DiscoveredMocapCam:
    name: str
    host: str
    port: int


def discover(timeout_seconds: float = 5.0) -> list[DiscoveredMocapCam]:
    try:
        from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf
    except ImportError as exc:
        raise RuntimeError("Install zeroconf to use discovery: python -m pip install zeroconf") from exc

    devices: dict[str, DiscoveredMocapCam] = {}

    class Listener(ServiceListener):
        def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            info = zeroconf.get_service_info(service_type, name)
            if info is None:
                return
            addresses = _preferred_addresses(info, IPVersion)
            if not addresses:
                return
            devices[name] = DiscoveredMocapCam(name=name, host=addresses[0], port=info.port)

        def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            self.add_service(zeroconf, service_type, name)

        def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            devices.pop(name, None)

    zeroconf = Zeroconf()
    try:
        ServiceBrowser(zeroconf, SERVICE_TYPE, Listener())
        time.sleep(timeout_seconds)
        return sorted(devices.values(), key=lambda device: device.name)
    finally:
        zeroconf.close()


def _preferred_addresses(info: "ServiceInfo", ip_version: type["IPVersion"]) -> list[str]:
    ipv4_addresses = [address for address in info.parsed_scoped_addresses(ip_version.V4Only) if address]
    if ipv4_addresses:
        return ipv4_addresses
    return [address for address in info.parsed_scoped_addresses(ip_version.All) if address]
