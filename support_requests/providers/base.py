from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from support_requests.models import SupportDestination, SupportProviderConfig, SupportRequest


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
