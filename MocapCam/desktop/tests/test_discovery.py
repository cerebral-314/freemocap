from freemocap_capture_server.discovery import SERVICE_PROTOCOL, SERVICE_TYPE


def test_bonjour_service_type_label_is_dns_sd_compatible():
    service_label = SERVICE_PROTOCOL.removeprefix("_").split("._", maxsplit=1)[0]

    assert SERVICE_PROTOCOL.startswith("_")
    assert SERVICE_PROTOCOL.endswith("._tcp")
    assert len(service_label.encode("utf-8")) <= 15
    assert SERVICE_TYPE == f"{SERVICE_PROTOCOL}.local."
