from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.module_loading import import_string

from support_requests.providers.base import SupportAttachmentLink

if TYPE_CHECKING:
    from support_requests.models import SupportRequest

ExtraAttachmentProvider = Callable[["SupportRequest"], Sequence[SupportAttachmentLink]]

DEFAULT_PROVIDER_BACKENDS: dict[str, str] = {
    "github": "support_requests.providers.github.GitHubSupportProvider",
}
DEFAULT_ATTACHMENT_SETTINGS = {
    "max_attachment_size_mb": 10,
    "max_attachments_per_request": 8,
}


def get_provider_backends() -> dict[str, str]:
    configured = getattr(settings, "SUPPORT_REQUESTS_PROVIDER_BACKENDS", {})
    return {
        **DEFAULT_PROVIDER_BACKENDS,
        **{str(key): str(value) for key, value in dict(configured).items()},
    }


def get_extra_attachment_providers() -> tuple[ExtraAttachmentProvider, ...]:
    providers = getattr(settings, "SUPPORT_REQUESTS_EXTRA_ATTACHMENT_PROVIDERS", [])
    loaded: list[ExtraAttachmentProvider] = []
    for provider in providers:
        resolved = import_string(provider) if isinstance(provider, str) else provider
        if not callable(resolved):
            msg = "SUPPORT_REQUESTS_EXTRA_ATTACHMENT_PROVIDERS entries must be callable."
            raise TypeError(msg)
        loaded.append(resolved)
    return tuple(loaded)


def get_attachment_settings() -> dict[str, int]:
    configured = getattr(settings, "SUPPORT_REQUESTS", {})
    if not isinstance(configured, dict):
        msg = "SUPPORT_REQUESTS must be a dict of attachment setting overrides."
        raise TypeError(msg)
    merged = {
        **DEFAULT_ATTACHMENT_SETTINGS,
        **configured,
    }
    return {key: int(merged[key]) for key in DEFAULT_ATTACHMENT_SETTINGS}


def attachment_max_size_bytes() -> int:
    return get_attachment_settings()["max_attachment_size_mb"] * 1024 * 1024


def max_attachments_per_request() -> int:
    return get_attachment_settings()["max_attachments_per_request"]
