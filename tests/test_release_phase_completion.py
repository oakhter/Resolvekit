from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_fresh_machine_launch_gate_script_exists():
    text = (ROOT / "scripts" / "fresh_machine_launch_gate.sh").read_text()

    assert "git clone" in text
    assert "make doctor" in text
    assert "ACTIVE_PROVIDER=mock" in text
    assert "scripts/public_smoke.sh" in text


def test_release_doctor_publish_script_exists():
    text = (ROOT / "scripts" / "publish_release_doctor.sh").read_text()

    assert "make doctor" in text
    assert "diagnostics/demo_doctor/latest.md" in text
    assert "release_commit" in text


def test_xlsx_is_public_preview_format_for_validation_and_preview():
    app_py = (ROOT / "backend" / "api" / "app.py").read_text()
    validate_sources = (ROOT / "scripts" / "validate_sources.py").read_text()
    readme = (ROOT / "README.md").read_text()

    assert 'CONFIGURATOR_SOURCE_PREVIEW_SUFFIX_ALLOWLIST = {".csv", ".xlsx"}' in app_py
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in app_py
    assert '".xlsx"' in validate_sources
    assert "CSV and XLSX" in readme


def test_router_and_orchestrator_refactor_modules_exist():
    assert (ROOT / "backend" / "api" / "routers" / "health.py").exists()
    assert (ROOT / "backend" / "core" / "orchestrator_response.py").exists()

    app_py = (ROOT / "backend" / "api" / "app.py").read_text()
    orchestrator_py = (ROOT / "backend" / "core" / "orchestrator.py").read_text()

    assert "app.include_router(health_router)" in app_py
    assert "from backend.core.orchestrator_response import" in orchestrator_py
