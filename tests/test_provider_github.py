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


def test_github_provider_creates_issue_with_attachment_links(monkeypatch: Any, db: Any) -> None:
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
        api_token="ghp_test",
    )
    destination = SupportDestination.objects.create(
        provider=provider_config,
        slug="github-destination",
        name="GitHub destination",
        remote_project="Solo-Host/demo",
        default_labels=["support", "triage"],
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
                "id": "9001",
                "number": 42,
                "html_url": "https://github.com/Solo-Host/demo/issues/42",
            }
        )

    monkeypatch.setattr("support_requests.providers.github.httpx.post", fake_post)

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
    assert captured["url"] == "https://api.github.com/repos/Solo-Host/demo/issues"
    assert captured["json"]["labels"] == ["support", "triage"]
    assert "screenshot.png" in captured["json"]["body"]
    assert "https://example.com/screenshot.png" in captured["json"]["body"]
