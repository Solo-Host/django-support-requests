from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, cast

from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework import serializers

from support_requests.conf import (
    attachment_max_size_bytes,
    get_extra_attachment_providers,
    get_provider_backends,
    max_attachments_per_request,
)
from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
    SupportRequestAttachment,
)
from support_requests.providers.base import BaseSupportProvider, SupportAttachmentLink


def validate_request_attachment(uploaded_file: UploadedFile) -> UploadedFile:
    max_size_bytes = attachment_max_size_bytes()
    max_size_mb = max_size_bytes // (1024 * 1024)
    file_size = int(uploaded_file.size or 0)
    if file_size > max_size_bytes:
        raise serializers.ValidationError(
            f"The attachment must be {max_size_mb} MB or smaller."
        )
    return uploaded_file


def create_request_attachment(
    *,
    request: SupportRequest,
    actor: Any,
    uploaded_file: UploadedFile,
    kind: str = SupportRequestAttachment.Kind.ATTACHMENT,
) -> SupportRequestAttachment:
    validate_request_attachment(uploaded_file)
    attachment_count = request.attachments.count()
    if attachment_count >= max_attachments_per_request():
        raise serializers.ValidationError(
            f"Only {max_attachments_per_request()} attachments are allowed per request."
        )

    return SupportRequestAttachment.objects.create(
        request=request,
        uploaded_by=actor,
        kind=kind,
        file=uploaded_file,
        original_filename=str(uploaded_file.name or "attachment.bin"),
        file_size=int(uploaded_file.size or 0),
        content_type=str(getattr(uploaded_file, "content_type", "") or ""),
    )


def append_request_message(
    *,
    request: SupportRequest,
    actor: Any,
    body: str,
    is_internal: bool = False,
    author_role: str | None = None,
) -> SupportMessage:
    resolved_author_role = author_role or _author_role_for_actor(actor)
    message = SupportMessage.objects.create(
        request=request,
        author=actor if getattr(actor, "is_authenticated", False) else None,
        author_role=resolved_author_role,
        body=body,
        is_internal=is_internal,
    )
    _update_request_activity(request=request, author_role=resolved_author_role)
    return message


def list_visible_messages(*, request: SupportRequest) -> Iterable[SupportMessage]:
    return cast(
        "Iterable[SupportMessage]",
        request.messages.filter(is_internal=False).select_related("author"),
    )


def collect_request_attachment_options(request: SupportRequest) -> list[SupportAttachmentLink]:
    collected: list[SupportAttachmentLink] = []
    for attachment in request.attachments.all():
        url = _attachment_url(attachment.file)
        collected.append(
            SupportAttachmentLink(
                key=f"package:{attachment.pk}",
                display_name=attachment.original_filename,
                url=url,
                kind=attachment.kind,
                content_type=attachment.content_type,
                source="package",
            )
        )

    seen_keys = {item.key for item in collected}
    for provider in get_extra_attachment_providers():
        for extra_attachment in provider(request):
            if extra_attachment.key in seen_keys:
                msg = (
                    "Duplicate attachment key "
                    f"'{extra_attachment.key}' returned by extra provider."
                )
                raise ValueError(msg)
            collected.append(extra_attachment)
            seen_keys.add(extra_attachment.key)
    return collected


def resolve_selected_attachment_links(
    request: SupportRequest,
    selected_keys: Sequence[str],
) -> list[SupportAttachmentLink]:
    available = {
        attachment.key: attachment
        for attachment in collect_request_attachment_options(request)
    }
    resolved: list[SupportAttachmentLink] = []
    for key in selected_keys:
        attachment = available.get(str(key))
        if attachment is None:
            raise serializers.ValidationError(f"Unknown attachment selection '{key}'.")
        resolved.append(attachment)
    return resolved


def escalate_support_request(
    *,
    request: SupportRequest,
    destination: SupportDestination,
    actor: Any,
    selected_attachment_keys: Sequence[str],
) -> SupportEscalation:
    if not destination.is_active:
        raise serializers.ValidationError("The selected support destination is inactive.")
    if not destination.provider.is_active:
        raise serializers.ValidationError("The selected support provider is inactive.")

    attachments = resolve_selected_attachment_links(request, selected_attachment_keys)
    provider = get_support_provider_backend(destination.provider)
    try:
        result = provider.create_issue(
            provider_config=destination.provider,
            destination=destination,
            request=request,
            attachments=attachments,
        )
    except Exception as exc:
        escalation = SupportEscalation.objects.create(
            request=request,
            destination=destination,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
            status=SupportEscalation.Status.FAILED,
            title_snapshot=request.subject,
            body_snapshot=request.body,
            forwarded_attachments=[attachment.__dict__ for attachment in attachments],
            failure_message=str(exc),
        )
        append_request_message(
            request=request,
            actor=None,
            body=(
                f"Failed to open a remote issue in {destination.name}: {exc}"
            ),
            is_internal=True,
            author_role=SupportMessage.AuthorRole.SYSTEM,
        )
        raise serializers.ValidationError(str(exc)) from exc

    escalation = SupportEscalation.objects.create(
        request=request,
        destination=destination,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        status=SupportEscalation.Status.OPENED,
        remote_issue_id=result.remote_issue_id,
        remote_issue_number=result.remote_issue_number,
        remote_issue_url=result.remote_issue_url,
        title_snapshot=request.subject,
        body_snapshot=request.body,
        forwarded_attachments=[attachment.__dict__ for attachment in attachments],
        provider_response=result.provider_response,
    )
    request.last_escalated_at = timezone.now()
    request.status = SupportRequest.Status.PENDING
    request.save(update_fields=["last_escalated_at", "status", "updated_at"])
    append_request_message(
        request=request,
        actor=None,
        body=(
            f"Opened remote issue {result.remote_issue_number or result.remote_issue_id} "
            f"in {destination.name}."
        ),
        is_internal=True,
        author_role=SupportMessage.AuthorRole.SYSTEM,
    )
    return escalation


def get_support_provider_backend(provider_config: SupportProviderConfig) -> BaseSupportProvider:
    backends = get_provider_backends()
    try:
        backend_path = backends[provider_config.backend_key]
    except KeyError as exc:
        msg = f"Unsupported provider backend '{provider_config.backend_key}'."
        raise ValueError(msg) from exc
    backend_class = import_string(backend_path)
    backend = backend_class()
    if not isinstance(backend, BaseSupportProvider):
        msg = f"Provider backend '{backend_path}' must inherit BaseSupportProvider."
        raise TypeError(msg)
    return backend


def _author_role_for_actor(actor: Any) -> str:
    if actor is None:
        return SupportMessage.AuthorRole.SYSTEM
    if bool(getattr(actor, "is_staff", False)):
        return SupportMessage.AuthorRole.SUPPORT
    return SupportMessage.AuthorRole.USER


def _update_request_activity(*, request: SupportRequest, author_role: str) -> None:
    now = timezone.now()
    update_fields = ["updated_at"]
    if author_role == SupportMessage.AuthorRole.USER:
        request.last_requester_activity_at = now
        update_fields.append("last_requester_activity_at")
    else:
        request.last_staff_activity_at = now
        update_fields.append("last_staff_activity_at")
    request.save(update_fields=update_fields)


def _attachment_url(file_field: Any) -> str:
    try:
        return str(file_field.url)
    except Exception as exc:
        msg = "Attachments must expose a usable storage URL before escalation."
        raise ValueError(msg) from exc
