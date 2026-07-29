from __future__ import annotations

import hashlib
import hmac
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from django.core.files.uploadedfile import UploadedFile
from django.db import connection
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
from support_requests.providers.base import (
    BaseSupportProvider,
    SupportAttachmentLink,
)

OPENING_MESSAGE_DEDUP_WINDOW = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class SupportRequestThreadEntry:
    id: str
    ticket_id: str
    created_at: datetime
    author_id: str | None
    author_role: str
    body: str
    is_internal: bool = False


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


def request_escalation_errors(request: SupportRequest) -> list[str]:
    errors: list[str] = []
    if request.pk is None:
        errors.append("Save the support request before opening a remote issue.")
    if request.requester_id is None:
        errors.append("Assign a requester before opening a remote issue.")
    if not str(request.subject or "").strip():
        errors.append("Add a subject before opening a remote issue.")
    if not str(request.body or "").strip():
        errors.append("Add a description before opening a remote issue.")
    return errors


def request_can_be_escalated(request: SupportRequest) -> bool:
    return not request_escalation_errors(request)


def append_request_message(
    *,
    request: SupportRequest,
    actor: Any,
    body: str,
    is_internal: bool = False,
    author_role: str | None = None,
    external_message_id: str = "",
    propagate_to_remote: bool = True,
) -> SupportMessage:
    resolved_author_role = author_role or _author_role_for_actor(actor)
    message = SupportMessage.objects.create(
        request=request,
        author=actor if getattr(actor, "is_authenticated", False) else None,
        author_role=resolved_author_role,
        body=body,
        is_internal=is_internal,
        external_message_id=external_message_id,
    )
    _update_request_activity(request=request, author_role=resolved_author_role)
    should_sync_to_remote = (
        propagate_to_remote
        and not is_internal
        and resolved_author_role == SupportMessage.AuthorRole.USER
    )
    if should_sync_to_remote:
        _forward_request_message_to_remote_issue(message)
    return message


def list_visible_messages(*, request: SupportRequest) -> Iterable[SupportMessage]:
    return cast(
        "Iterable[SupportMessage]",
        request.messages.filter(is_internal=False).select_related("author"),
    )


def list_request_thread_entries(
    *,
    request: SupportRequest,
    include_internal: bool = False,
) -> list[SupportRequestThreadEntry]:
    messages = list(request.messages.all())
    if not include_internal:
        messages = [message for message in messages if not message.is_internal]

    thread_entries: list[SupportRequestThreadEntry] = []
    if _request_needs_opening_message_entry(request=request, messages=messages):
        thread_entries.append(
            SupportRequestThreadEntry(
                id=f"opening-{request.pk}",
                ticket_id=str(request.pk),
                created_at=request.created_at,
                author_id=str(request.requester_id) if request.requester_id else None,
                author_role=SupportMessage.AuthorRole.USER,
                body=request.body,
                is_internal=False,
            )
        )

    thread_entries.extend(_thread_entry_from_message(message) for message in messages)
    return thread_entries


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
    if errors := request_escalation_errors(request):
        raise serializers.ValidationError({"non_field_errors": errors})
    if not destination.is_active:
        raise serializers.ValidationError("The selected support destination is inactive.")
    if not destination.provider.is_active:
        raise serializers.ValidationError("The selected support provider is inactive.")
    if request.escalations.filter(status=SupportEscalation.Status.OPENED).exists():
        raise serializers.ValidationError(
            "This support request already has an opened remote issue."
        )

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
        SupportEscalation.objects.create(
            request=request,
            destination=destination,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
            status=SupportEscalation.Status.FAILED,
            title_snapshot=request.subject,
            body_snapshot=request.body,
            forwarded_attachments=_json_storage_safe(
                [attachment.__dict__ for attachment in attachments]
            ),
            failure_message=str(exc),
        )
        append_request_message(
            request=request,
            actor=None,
            body=f"Failed to open a remote issue in {destination.name}: {exc}",
            is_internal=True,
            author_role=SupportMessage.AuthorRole.SYSTEM,
            propagate_to_remote=False,
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
        forwarded_attachments=_json_storage_safe(
            [attachment.__dict__ for attachment in attachments]
        ),
        provider_response=_json_storage_safe(result.provider_response),
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
        propagate_to_remote=False,
    )
    return escalation


def github_webhook_signature_is_valid(
    *,
    raw_body: bytes,
    signature_header: str,
    provider_config: SupportProviderConfig,
) -> bool:
    secret = str(provider_config.github_webhook_secret or "")
    if not secret or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header, f"sha256={expected}")


def github_provider_for_webhook(payload: Mapping[str, Any]) -> SupportProviderConfig | None:
    installation_id = str(payload.get("installation", {}).get("id", "")).strip()
    if not installation_id:
        return None
    return SupportProviderConfig.objects.filter(
        backend_key="github",
        github_installation_id=installation_id,
        is_active=True,
    ).first()


def handle_github_webhook(
    *,
    provider_config: SupportProviderConfig,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    event_name = str(payload.get("_event_name", "")).strip()
    if event_name == "issue_comment":
        return _handle_github_issue_comment_event(provider_config=provider_config, payload=payload)
    if event_name == "issues":
        return _handle_github_issue_event(provider_config=provider_config, payload=payload)
    return {"status": "ignored", "reason": f"Unsupported event '{event_name}'."}


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


def _forward_request_message_to_remote_issue(message: SupportMessage) -> None:
    escalation = (
        message.request.escalations.filter(status=SupportEscalation.Status.OPENED)
        .select_related("destination__provider")
        .first()
    )
    if escalation is None:
        return

    provider = get_support_provider_backend(escalation.destination.provider)
    try:
        result = provider.add_comment(
            provider_config=escalation.destination.provider,
            destination=escalation.destination,
            escalation=escalation,
            message=message,
        )
    except Exception as exc:
        append_request_message(
            request=message.request,
            actor=None,
            body=(
                f"Failed to sync requester update to {escalation.destination.name}: {exc}"
            ),
            is_internal=True,
            author_role=SupportMessage.AuthorRole.SYSTEM,
            propagate_to_remote=False,
        )
        return

    message.external_message_id = result.remote_comment_id
    message.save(update_fields=["external_message_id", "updated_at"])


def _thread_entry_from_message(message: SupportMessage) -> SupportRequestThreadEntry:
    return SupportRequestThreadEntry(
        id=str(message.pk),
        ticket_id=str(message.request_id),
        created_at=message.created_at,
        author_id=str(message.author_id) if message.author_id is not None else None,
        author_role=message.author_role,
        body=message.body,
        is_internal=message.is_internal,
    )


def _request_needs_opening_message_entry(
    *,
    request: SupportRequest,
    messages: list[SupportMessage],
) -> bool:
    opening_body = str(request.body or "").strip()
    if not opening_body:
        return False

    for message in messages:
        if message.is_internal or message.author_role != SupportMessage.AuthorRole.USER:
            continue
        if str(message.body or "").strip() != opening_body:
            continue
        if (
            abs((message.created_at - request.created_at).total_seconds())
            <= OPENING_MESSAGE_DEDUP_WINDOW.total_seconds()
        ):
            return False

    return True


def _json_storage_safe(value: Any) -> Any:
    if not _database_uses_sql_ascii():
        return value

    if isinstance(value, dict):
        return {
            _ascii_safe_string(str(key)): _json_storage_safe(item_value)
            for key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_json_storage_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_storage_safe(item) for item in value]
    if isinstance(value, str):
        return _ascii_safe_string(value)
    return value


def _ascii_safe_string(value: str) -> str:
    replacements = {
        "\u00b7": " - ",
        "\u2013": "-",
        "\u2014": "--",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    normalized_value = value
    for source, replacement in replacements.items():
        normalized_value = normalized_value.replace(source, replacement)

    ascii_value = (
        unicodedata.normalize("NFKD", normalized_value).encode("ascii", "replace").decode("ascii")
    )
    return ascii_value


def _database_uses_sql_ascii() -> bool:
    if connection.vendor != "postgresql":
        return False

    raw_connection = connection.connection
    if raw_connection is not None:
        info = getattr(raw_connection, "info", None)
        if info is not None:
            encoding = getattr(info, "encoding", None)
            if encoding:
                return str(encoding).upper().replace("-", "_") == "SQL_ASCII"

    with connection.cursor() as cursor:
        cursor.execute("SHOW server_encoding")
        row = cursor.fetchone()
    if not row:
        return False
    return str(row[0]).upper().replace("-", "_") == "SQL_ASCII"


def _handle_github_issue_comment_event(
    *,
    provider_config: SupportProviderConfig,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    action = str(payload.get("action", "")).strip()
    if action != "created":
        return {"status": "ignored", "reason": f"Unsupported issue_comment action '{action}'."}

    issue_payload = cast(dict[str, Any], payload.get("issue", {}))
    if "pull_request" in issue_payload:
        return {"status": "ignored", "reason": "Pull request comment webhooks are ignored."}

    escalation = _matching_github_escalation(provider_config=provider_config, payload=payload)
    if escalation is None:
        return {"status": "ignored", "reason": "No matching local support request found."}

    comment_payload = cast(dict[str, Any], payload.get("comment", {}))
    comment_id = str(comment_payload.get("id", "")).strip()
    body = str(comment_payload.get("body", "")).strip()
    if not comment_id or not body:
        return {"status": "ignored", "reason": "Comment payload was incomplete."}
    if escalation.request.messages.filter(external_message_id=comment_id).exists():
        return {"status": "ignored", "reason": "Remote comment already synced."}

    append_request_message(
        request=escalation.request,
        actor=None,
        body=body,
        author_role=SupportMessage.AuthorRole.SUPPORT,
        external_message_id=comment_id,
        propagate_to_remote=False,
    )
    return {"status": "synced", "reason": "Remote support comment copied to local request."}


def _handle_github_issue_event(
    *,
    provider_config: SupportProviderConfig,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    action = str(payload.get("action", "")).strip()
    if action not in {"closed", "reopened"}:
        return {"status": "ignored", "reason": f"Unsupported issues action '{action}'."}

    issue_payload = cast(dict[str, Any], payload.get("issue", {}))
    if "pull_request" in issue_payload:
        return {"status": "ignored", "reason": "Pull request issue events are ignored."}

    escalation = _matching_github_escalation(provider_config=provider_config, payload=payload)
    if escalation is None:
        return {"status": "ignored", "reason": "No matching local support request found."}

    if action == "closed":
        escalation.request.status = SupportRequest.Status.RESOLVED
        escalation.request.resolved_at = timezone.now()
    else:
        escalation.request.status = SupportRequest.Status.OPEN
        escalation.request.resolved_at = None
    escalation.request.save(update_fields=["status", "resolved_at", "updated_at"])
    return {
        "status": "synced",
        "reason": f"Request status updated from remote issue action '{action}'.",
    }


def _matching_github_escalation(
    *,
    provider_config: SupportProviderConfig,
    payload: Mapping[str, Any],
) -> SupportEscalation | None:
    repository_full_name = str(payload.get("repository", {}).get("full_name", "")).strip()
    issue_number = str(payload.get("issue", {}).get("number", "")).strip()
    if not repository_full_name or not issue_number:
        return None
    return (
        SupportEscalation.objects.filter(
            destination__provider=provider_config,
            destination__remote_project__iexact=repository_full_name,
            remote_issue_number=issue_number,
            status=SupportEscalation.Status.OPENED,
        )
        .select_related("request", "destination", "destination__provider")
        .first()
    )
