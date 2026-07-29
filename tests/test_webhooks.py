from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
)
from tests.helpers import create_user

pytestmark = pytest.mark.django_db


def _github_signature(secret: str, payload: dict[str, Any]) -> str:
    raw_body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_issue_comment_webhook_syncs_support_reply(client: Any) -> None:
    provider = SupportProviderConfig.objects.create(
        name="GitHub Support",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.GITHUB_APP,
        github_app_id="12345",
        github_installation_id="67890",
        github_private_key="-----BEGIN PRIVATE KEY-----\\nTEST\\n-----END PRIVATE KEY-----",
        github_webhook_secret="webhook-secret",
    )
    destination = SupportDestination.objects.create(
        provider=provider,
        name="Aspenroute backend issues",
        remote_project="Solo-Host/demo",
    )
    support_request = SupportRequest.objects.create(
        requester=create_user(email="user@example.com"),
        subject="Need help",
        body="Initial message",
    )
    SupportEscalation.objects.create(
        request=support_request,
        destination=destination,
        status=SupportEscalation.Status.OPENED,
        remote_issue_id="9001",
        remote_issue_number="42",
        remote_issue_url="https://github.com/Solo-Host/demo/issues/42",
        title_snapshot=support_request.subject,
        body_snapshot=support_request.body,
    )
    payload = {
        "installation": {"id": 67890},
        "repository": {"full_name": "Solo-Host/demo"},
        "issue": {"number": 42},
        "comment": {"id": 555, "body": "We are looking into this now."},
        "sender": {"login": "support-agent", "type": "User"},
        "action": "created",
    }

    response = client.post(
        "/api/support/webhooks/github/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="issue_comment",
        HTTP_X_HUB_SIGNATURE_256=_github_signature("webhook-secret", payload),
    )

    assert response.status_code == 200
    assert SupportMessage.objects.filter(
        request=support_request,
        author_role=SupportMessage.AuthorRole.SUPPORT,
        body="We are looking into this now.",
        external_message_id="555",
    ).count() == 1


def test_github_issue_webhook_updates_local_request_status(client: Any) -> None:
    provider = SupportProviderConfig.objects.create(
        name="GitHub Support",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.GITHUB_APP,
        github_app_id="12345",
        github_installation_id="67890",
        github_private_key="-----BEGIN PRIVATE KEY-----\\nTEST\\n-----END PRIVATE KEY-----",
        github_webhook_secret="webhook-secret",
    )
    destination = SupportDestination.objects.create(
        provider=provider,
        name="Aspenroute backend issues",
        remote_project="Solo-Host/demo",
    )
    support_request = SupportRequest.objects.create(
        requester=create_user(email="user@example.com"),
        subject="Need help",
        body="Initial message",
        status=SupportRequest.Status.PENDING,
    )
    SupportEscalation.objects.create(
        request=support_request,
        destination=destination,
        status=SupportEscalation.Status.OPENED,
        remote_issue_id="9001",
        remote_issue_number="42",
        remote_issue_url="https://github.com/Solo-Host/demo/issues/42",
        title_snapshot=support_request.subject,
        body_snapshot=support_request.body,
    )
    payload = {
        "installation": {"id": 67890},
        "repository": {"full_name": "Solo-Host/demo"},
        "issue": {"number": 42},
        "action": "closed",
    }

    response = client.post(
        "/api/support/webhooks/github/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="issues",
        HTTP_X_HUB_SIGNATURE_256=_github_signature("webhook-secret", payload),
    )

    assert response.status_code == 200
    support_request.refresh_from_db()
    assert support_request.status == SupportRequest.Status.RESOLVED


def test_github_webhook_rejects_invalid_signature(client: Any) -> None:
    provider = SupportProviderConfig.objects.create(
        name="GitHub Support",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.GITHUB_APP,
        github_app_id="12345",
        github_installation_id="67890",
        github_private_key="-----BEGIN PRIVATE KEY-----\\nTEST\\n-----END PRIVATE KEY-----",
        github_webhook_secret="webhook-secret",
    )
    SupportDestination.objects.create(
        provider=provider,
        name="Aspenroute backend issues",
        remote_project="Solo-Host/demo",
    )
    payload = {
        "installation": {"id": 67890},
        "repository": {"full_name": "Solo-Host/demo"},
        "issue": {"number": 42},
        "comment": {"id": 555, "body": "We are looking into this now."},
        "action": "created",
    }

    response = client.post(
        "/api/support/webhooks/github/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="issue_comment",
        HTTP_X_HUB_SIGNATURE_256="sha256=bad",
    )

    assert response.status_code == 403
