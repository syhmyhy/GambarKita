import run


def test_banner_lists_local_and_network_urls_when_listening_everywhere(settings_factory, monkeypatch) -> None:
    monkeypatch.setattr(run, "lan_address", lambda: "192.168.5.240")
    banner = run.startup_banner(settings_factory(host="0.0.0.0", port=8000))
    assert "Local:    http://127.0.0.1:8000" in banner
    assert "Network:  http://192.168.5.240:8000" in banner
    assert "API docs: http://127.0.0.1:8000/api/docs" in banner
    assert "0.0.0.0" not in banner  # not a clickable address


def test_banner_shows_the_bound_address_and_hides_docs_when_disabled(settings_factory) -> None:
    banner = run.startup_banner(settings_factory(host="127.0.0.1", port=9000, enable_docs=False))
    assert "Address:  http://127.0.0.1:9000" in banner
    assert "Network" not in banner and "/api/docs" not in banner


def test_lan_address_is_never_loopback() -> None:
    address = run.lan_address()
    assert address is None or not address.startswith("127.")
