from pathlib import Path

from scripts.os_detect import detect_os


ROOT = Path(__file__).resolve().parent.parent


def test_admin_ui_snapshot_has_launch_control_sections():
    html = (ROOT / "frontend" / "admin" / "index.html").read_text()

    for marker in [
        "ResolveKit Admin",
        "Launch Control",
        "Analytics Report",
        "Launch Readiness",
        "Replay Lab",
        "A/B Experiments",
    ]:
        assert marker in html


def test_configurator_ui_snapshot_has_runtime_config_sections():
    html = (ROOT / "frontend" / "configurator" / "index.html").read_text()

    for marker in [
        "ResolveKit Configurator",
        "source-preview",
        "diagnostics.test_all.started",
        "support_context_bundles",
        "reload",
    ]:
        assert marker in html


def test_os_detect_returns_supported_shape_for_current_platform():
    info = detect_os().to_dict()

    assert info["os_family"] in {"macos", "linux", "wsl", "windows", "unsupported"}
    assert info["label"]
    assert "docker_install_hint" in info
    assert "python_install_hint" in info
