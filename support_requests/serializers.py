from __future__ import annotations

from typing import Any, cast

from django.utils import timezone
from rest_framework import serializers

from support_requests.models import SupportMessage, SupportRequest, SupportRequestAttachment
from support_requests.services import (
    append_request_message,
    create_request_attachment,
    list_request_thread_entries,
)


class SupportMessageSerializer(serializers.ModelSerializer[SupportMessage]):
    ticket = serializers.UUIDField(source="request_id", read_only=True)

    class Meta:
        model = SupportMessage
        fields = (
            "id",
            "ticket",
            "created_at",
            "author",
            "author_role",
            "body",
            "is_internal",
        )
        read_only_fields = (
            "id",
            "ticket",
            "created_at",
            "author",
            "author_role",
            "is_internal",
        )


class SupportRequestThreadEntrySerializer(serializers.Serializer):
    id = serializers.CharField()
    ticket = serializers.CharField(source="ticket_id")
    created_at = serializers.DateTimeField()
    author = serializers.CharField(source="author_id", allow_null=True)
    author_role = serializers.CharField()
    body = serializers.CharField()
    is_internal = serializers.BooleanField()


class SupportRequestAttachmentSerializer(serializers.ModelSerializer[SupportRequestAttachment]):
    file_url = serializers.FileField(source="file", read_only=True)

    class Meta:
        model = SupportRequestAttachment
        fields = (
            "id",
            "kind",
            "file_url",
            "original_filename",
            "file_size",
            "content_type",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class SupportRequestAttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    kind = serializers.ChoiceField(
        choices=SupportRequestAttachment.Kind.choices,
        required=False,
        default=SupportRequestAttachment.Kind.ATTACHMENT,
    )


class SupportRequestSerializer(serializers.ModelSerializer[SupportRequest]):
    messages = serializers.SerializerMethodField()
    attachments = SupportRequestAttachmentSerializer(many=True, read_only=True)
    attachment_files = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        write_only=True,
    )

    class Meta:
        model = SupportRequest
        fields = (
            "id",
            "subject",
            "body",
            "status",
            "priority",
            "category",
            "resolved_at",
            "last_requester_activity_at",
            "last_staff_activity_at",
            "last_escalated_at",
            "messages",
            "attachments",
            "attachment_files",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "resolved_at",
            "last_requester_activity_at",
            "last_staff_activity_at",
            "last_escalated_at",
            "messages",
            "attachments",
            "created_at",
            "updated_at",
        )

    def get_messages(self, obj: SupportRequest) -> list[dict[str, Any]]:
        serializer = SupportRequestThreadEntrySerializer(
            list_request_thread_entries(request=obj),
            many=True,
        )
        return cast(list[dict[str, Any]], serializer.data)

    def create(self, validated_data: dict[str, Any]) -> SupportRequest:
        attachment_files = cast(list[Any], validated_data.pop("attachment_files", []))
        request_context = self.context.get("request")
        if request_context is None or not request_context.user.is_authenticated:
            msg = "Authenticated users are required to create support requests."
            raise serializers.ValidationError(msg)
        now = timezone.now()
        support_request = SupportRequest.objects.create(
            requester=request_context.user,
            last_requester_activity_at=now,
            **validated_data,
        )
        for uploaded_file in attachment_files:
            create_request_attachment(
                request=support_request,
                actor=request_context.user,
                uploaded_file=uploaded_file,
            )
        return support_request


class SupportRequestMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(trim_whitespace=True)

    def create_for_request(self, *, request: SupportRequest, actor: Any) -> SupportMessage:
        body = cast(str, self.validated_data["body"]).strip()
        return append_request_message(request=request, actor=actor, body=body)
