from __future__ import annotations

from typing import Any

from support_requests.models import SupportDestination, SupportProviderConfig, SupportRequest
from support_requests.providers.base import SupportAttachmentLink
from support_requests.providers.github import GitHubSupportProvider
from tests.helpers import create_user


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_github_provider_uses_github_app_installation_token(monkeypatch: Any, db: Any) -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    provider_config = SupportProviderConfig.objects.create(
        slug="github",
        name="GitHub",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.GITHUB_APP,
        github_app_id="12345",
        github_installation_id="67890",
        github_private_key="-----BEGIN PRIVATE KEY-----\\nTEST\\n-----END PRIVATE KEY-----",
    )
    destination = SupportDestination.objects.create(
        provider=provider_config,
        slug="github-destination",
        name="GitHub destination",
        remote_project="Solo-Host/demo",
        default_labels=["support", "triage"],
    )
    calls: list[dict[str, Any]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> _FakeResponse:
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if url.endswith("/access_tokens"):
            return _FakeResponse({"token": "ghs_installation_token"})
        return _FakeResponse(
            {
                "id": "9001",
                "number": 42,
                "html_url": "https://github.com/Solo-Host/demo/issues/42",
            }
        )

    monkeypatch.setattr("support_requests.providers.github.httpx.post", fake_post)
    monkeypatch.setattr(
        "support_requests.providers.github.jwt.encode",
        lambda *args, **kwargs: "app.jwt",
    )

    result = GitHubSupportProvider().create_issue(
        provider_config=provider_config,
        destination=destination,
        request=support_request,
        attachments=[
            SupportAttachmentLink(
                key="package:1",
                display_name="screenshot.png",
                url="https://example.com/screenshot.png",
                kind="screenshot",
                content_type="image/png",
            )
        ],
    )

    assert result.remote_issue_number == "42"
    assert calls[0]["url"] == "https://api.github.com/app/installations/67890/access_tokens"
    assert calls[0]["headers"]["Authorization"].startswith("Bearer ")
    assert calls[1]["url"] == "https://api.github.com/repos/Solo-Host/demo/issues"
    assert calls[1]["headers"]["Authorization"] == "Bearer ghs_installation_token"
    assert calls[1]["json"]["labels"] == ["support", "triage"]
    assert "screenshot.png" in calls[1]["json"]["body"]
    assert "https://example.com/screenshot.png" in calls[1]["json"]["body"]


def test_github_provider_can_fall_back_to_api_token(monkeypatch: Any, db: Any) -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    provider_config = SupportProviderConfig.objects.create(
        slug="github-pat",
        name="GitHub PAT",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.API_TOKEN,
        api_token="ghp_legacy_fallback",
    )
    destination = SupportDestination.objects.create(
        provider=provider_config,
        slug="github-destination-pat",
        name="GitHub destination",
        remote_project="Solo-Host/demo",
    )
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "id": "9002",
                "number": 43,
                "html_url": "https://github.com/Solo-Host/demo/issues/43",
            }
        )

    monkeypatch.setattr("support_requests.providers.github.httpx.post", fake_post)

    result = GitHubSupportProvider().create_issue(
        provider_config=provider_config,
        destination=destination,
        request=support_request,
        attachments=[],
    )

    assert result.remote_issue_number == "43"
    assert captured["headers"]["Authorization"] == "Bearer ghp_legacy_fallback"
