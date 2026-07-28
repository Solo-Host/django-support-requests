from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from django.utils import timezone

from support_requests.models import SupportProviderConfig
from support_requests.providers.base import (
    BaseSupportProvider,
    SupportAttachmentLink,
    SupportCommentResult,
    SupportIssueResult,
)


class GitHubSupportProvider(BaseSupportProvider):
    backend_key = "github"
    default_base_url = "https://api.github.com"

    def create_issue(
        self,
        *,
        provider_config: SupportProviderConfig,
        destination: Any,
        request: Any,
        attachments: list[SupportAttachmentLink],
    ) -> SupportIssueResult:
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
        token = self._resolve_access_token(provider_config=provider_config, base_url=base_url)
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

    def add_comment(
        self,
        *,
        provider_config: SupportProviderConfig,
        destination: Any,
        escalation: Any,
        message: Any,
    ) -> SupportCommentResult:
        remote_project = str(destination.remote_project).strip()
        issue_number = str(escalation.remote_issue_number).strip()
        if not remote_project or not issue_number:
            msg = "The remote issue is missing repository or issue number details."
            raise ValueError(msg)

        base_url = str(provider_config.base_url or self.default_base_url).rstrip("/")
        token = self._resolve_access_token(provider_config=provider_config, base_url=base_url)
        comment_url = f"{base_url}/repos/{remote_project}/issues/{issue_number}/comments"
        response = httpx.post(
            comment_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "django-support-requests",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": str(message.body).strip()},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return SupportCommentResult(
            remote_comment_id=str(data.get("id", "")),
            remote_comment_url=str(data.get("html_url", "")),
            provider_response=data,
        )

    def _resolve_access_token(
        self,
        *,
        provider_config: SupportProviderConfig,
        base_url: str,
    ) -> str:
        if provider_config.auth_mode == SupportProviderConfig.AuthMode.GITHUB_APP:
            return self._installation_access_token(
                provider_config=provider_config,
                base_url=base_url,
            )

        token = str(provider_config.api_token).strip()
        if not token:
            msg = f"Provider '{provider_config.name}' is missing an API token."
            raise ValueError(msg)
        return token

    def _installation_access_token(
        self,
        *,
        provider_config: SupportProviderConfig,
        base_url: str,
    ) -> str:
        app_id = str(provider_config.github_app_id).strip()
        installation_id = str(provider_config.github_installation_id).strip()
        private_key = str(provider_config.github_private_key).strip().replace("\\n", "\n")
        if not app_id or not installation_id or not private_key:
            msg = (
                f"Provider '{provider_config.name}' is missing GitHub App credentials. "
                "Set auth_mode to GitHub App and provide app ID, installation ID, and private key."
            )
            raise ValueError(msg)

        now = datetime.now(tz=UTC)
        app_jwt = jwt.encode(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": app_id,
            },
            private_key,
            algorithm="RS256",
        )
        token_response = httpx.post(
            f"{base_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "User-Agent": "django-support-requests",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={},
            timeout=30.0,
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        token = str(token_payload.get("token", "")).strip()
        if not token:
            msg = "GitHub App installation token response did not include a token."
            raise ValueError(msg)
        return token

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
