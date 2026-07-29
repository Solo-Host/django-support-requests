from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from support_requests.models import (
        SupportDestination,
        SupportEscalation,
        SupportMessage,
        SupportProviderConfig,
        SupportRequest,
    )


@dataclass(frozen=True)
class SupportAttachmentLink:
    key: str
    display_name: str
    url: str
    kind: str
    content_type: str = ""
    source: str = "package"


@dataclass(frozen=True)
class SupportIssueResult:
    remote_issue_id: str
    remote_issue_number: str
    remote_issue_url: str
    provider_response: dict[str, Any]


@dataclass(frozen=True)
class SupportCommentResult:
    remote_comment_id: str
    remote_comment_url: str
    provider_response: dict[str, Any]


class BaseSupportProvider(ABC):
    backend_key: str

    @abstractmethod
    def create_issue(
        self,
        *,
        provider_config: SupportProviderConfig,
        destination: SupportDestination,
        request: SupportRequest,
        attachments: list[SupportAttachmentLink],
    ) -> SupportIssueResult:
        raise NotImplementedError

    @abstractmethod
    def add_comment(
        self,
        *,
        provider_config: SupportProviderConfig,
        destination: SupportDestination,
        escalation: SupportEscalation,
        message: SupportMessage,
    ) -> SupportCommentResult:
        raise NotImplementedError
