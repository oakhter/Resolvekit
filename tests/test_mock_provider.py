from backend.core import config
from backend.providers import get_provider, reset_provider_cache


def test_mock_provider_returns_labeled_canned_draft_and_embedding(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_PROVIDER", "mock")
    reset_provider_cache()

    provider = get_provider()
    response = provider.complete("system", "Customer cannot sign in.")
    embedding = provider.get_embedding("Customer cannot sign in.", is_query=True)

    assert provider.get_name() == "mock"
    assert "MOCK PREVIEW" in response
    assert "Draft Email:" in response
    assert len(embedding) == 384
    assert any(value != 0 for value in embedding)

    reset_provider_cache()


def test_config_validation_allows_mock_without_hosted_provider_key(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_PROVIDER", "mock")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://demo:demo@localhost:5432/demo")
    monkeypatch.setattr(config, "API_KEY", "viewer-token-123")
    monkeypatch.setattr(config, "CONFIGURATOR_API_KEY", "admin-token-456")
    monkeypatch.setattr(config, "VIEWER_TOKEN", "trace-viewer-789")
    monkeypatch.setattr(config, "CONFIGURATOR_ADMIN_TOKEN", "config-admin-abc")

    config.validate()
