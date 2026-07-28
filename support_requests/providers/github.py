from __future__ import annotations

from typing import Any

import httpx
from django.utils import timezone

from support_requests.providers.base import (
    BaseSupportProvider,
    SupportAttachmentLink,
    SupportIssueResult,
)


class GitHubSupportProvider(BaseSupportProvider):
    backend_key = "github"
    default_base_url = "https://api.github.com"

    def create_issue(
        self,
        *,
        provider_config: Any,
        destination: Any,
        request: Any,
        attachments: list[SupportAttachmentLink],
    ) -> SupportIssueResult:
        token = str(provider_config.api_token).strip()
        if not token:
            msg = f"Provider '{provider_config.name}' is missing an API token."
            raise ValueError(msg)

        remote_project = str(destination.remote_project).strip()
        if "/" not in remote_project:
            msg = f"Destination '{destination.name}' must use owner/repo for GitHub."
            raise ValueError(msg)

        labels = destination.default_labels
        if not isinstance(labels, list):
            msg = f"Destination '{destination.name}' has invalid default_labels."
            raise ValueError(msg)

        base_url = str(provider_config.base_url or self.default_base_url).rstrip("/")
        issue_url = f"{base_url}/repos/{remote_project}/issues"
        payload = {
            "title": str(request.subject),
            "body": self._build_issue_body(request=request, attachments=attachments),
            "labels": [str(label) for label in labels],
        }
        response = httpx.post(
            issue_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "django-support-requests",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return SupportIssueResult(
            remote_issue_id=str(data.get("id", "")),
            remote_issue_number=str(data.get("number", "")),
            remote_issue_url=str(data.get("html_url", "")),
            provider_response=data,
        )

    def _build_issue_body(self, *, request: Any, attachments: list[SupportAttachmentLink]) -> str:
        requester = getattr(request, "requester", None)
        requester_label = ""
        if requester is not None:
            requester_label = (
                getattr(requester, "email", "")
                or getattr(requester, "username", "")
                or str(getattr(requester, "pk", ""))
            )

        lines = [
            "## Local support request",
            "",
            f"- Request ID: `{request.id}`",
            f"- Status: `{request.status}`",
            f"- Priority: `{request.priority}`",
            f"- Category: `{request.category}`",
            f"- Created: `{timezone.localtime(request.created_at).isoformat()}`",
        ]
        if requester_label:
            lines.append(f"- Requester: `{requester_label}`")
        lines.extend(
            [
                "",
                "## Request body",
                "",
                str(request.body).strip() or "_No body provided._",
            ]
        )

        visible_messages = list(
            request.messages.filter(is_internal=False).select_related("author").order_by("created_at")
        )
        if visible_messages:
            lines.extend(["", "## Visible conversation", ""])
            for message in visible_messages:
                author = getattr(message, "author", None)
                author_label = (
                    getattr(author, "email", "")
                    or getattr(author, "username", "")
                    or message.author_role
                )
                lines.extend(
                    [
                        (
                            "### "
                            f"{author_label or message.author_role}"
                            f" · {timezone.localtime(message.created_at).isoformat()}"
                        ),
                        "",
                        str(message.body).strip() or "_No message body._",
                        "",
                    ]
                )

        if attachments:
            lines.extend(["", "## Attachments", ""])
            for attachment in attachments:
                lines.append(
                    f"- [{attachment.display_name}]({attachment.url})"
                    f" — `{attachment.kind}`"
                    + (
                        f" (`{attachment.content_type}`)"
                        if attachment.content_type
                        else ""
                    )
                )

        lines.append("")
        return "\n".join(lines)
