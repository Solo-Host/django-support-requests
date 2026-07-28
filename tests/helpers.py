from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model

from support_requests.providers.base import (
    BaseSupportProvider,
    SupportAttachmentLink,
    SupportIssueResult,
)


def create_user(*, email: str, is_staff: bool = False, is_superuser: bool = False) -> Any:
    user_model = get_user_model()
    username = email.split("@", 1)[0]
    return user_model.objects.create_user(
        username=username,
        email=email,
        password="test-pass-123",
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def extra_attachment_provider(_request: Any) -> list[SupportAttachmentLink]:
    return [
        SupportAttachmentLink(
            key="extra:diagnostics",
            display_name="diagnostics.zip",
            url="https://example.com/diagnostics.zip",
            kind="log_bundle",
            content_type="application/zip",
            source="extra",
        )
    ]


class DummySupportProvider(BaseSupportProvider):
    backend_key = "dummy"

    def create_issue(
        self,
        *,
        provider_config: Any,
        destination: Any,
        request: Any,
        attachments: list[SupportAttachmentLink],
    ) -> SupportIssueResult:
        del provider_config
        return SupportIssueResult(
            remote_issue_id=f"{request.id}",
            remote_issue_number="101",
            remote_issue_url=f"https://example.com/{destination.remote_project}/issues/101",
            provider_response={
                "attachments": [attachment.display_name for attachment in attachments],
            },
        )
