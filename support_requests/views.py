"""Support request API views."""

from __future__ import annotations

from typing import Any, cast

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from support_requests.models import SupportRequest
from support_requests.serializers import (
    SupportMessageSerializer,
    SupportRequestAttachmentSerializer,
    SupportRequestAttachmentUploadSerializer,
    SupportRequestMessageCreateSerializer,
    SupportRequestSerializer,
)
from support_requests.services import (
    create_request_attachment,
    list_visible_messages,
)


class SupportRequestViewSet(viewsets.ModelViewSet):
    queryset = SupportRequest.objects.all().prefetch_related("messages", "attachments")
    serializer_class = SupportRequestSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return queryset.none()
        return queryset.filter(requester=user)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request: Request, pk: str | None = None) -> Response:
        support_request = self.get_object()
        if request.method == "GET":
            visible_messages = list(list_visible_messages(request=support_request))
            serializer = SupportMessageSerializer(
                visible_messages,
                many=True,
            )
            return Response(serializer.data)

        message_create_serializer = SupportRequestMessageCreateSerializer(data=request.data)
        message_create_serializer.is_valid(raise_exception=True)
        message = message_create_serializer.create_for_request(
            request=support_request,
            actor=cast(Any, request.user),
        )
        return Response(
            SupportMessageSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="attachments")
    def attachments(self, request: Request, pk: str | None = None) -> Response:
        support_request = self.get_object()
        if request.method == "GET":
            serializer = SupportRequestAttachmentSerializer(
                support_request.attachments.all(),
                many=True,
            )
            return Response(serializer.data)

        attachment_upload_serializer = SupportRequestAttachmentUploadSerializer(data=request.data)
        attachment_upload_serializer.is_valid(raise_exception=True)
        attachment = create_request_attachment(
            request=support_request,
            actor=cast(Any, request.user),
            uploaded_file=cast(Any, attachment_upload_serializer.validated_data["file"]),
            kind=cast(str, attachment_upload_serializer.validated_data["kind"]),
        )
        return Response(
            SupportRequestAttachmentSerializer(attachment).data,
            status=status.HTTP_201_CREATED,
        )
