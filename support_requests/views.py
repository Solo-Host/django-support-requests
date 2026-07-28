"""Support request API views."""

from __future__ import annotations

import json
from typing import Any, cast

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

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
    github_provider_for_webhook,
    github_webhook_signature_is_valid,
    handle_github_webhook,
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


class GitHubWebhookView(APIView):
    authentication_classes: list[type[Any]] = []
    permission_classes: list[type[Any]] = []

    def post(self, request: Request) -> Response:
        raw_body = request._request.body
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return Response(
                {"detail": "GitHub webhook payload must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(payload, dict):
            return Response(
                {"detail": "GitHub webhook payload must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = cast(dict[str, Any], payload)
        provider_config = github_provider_for_webhook(payload)
        if provider_config is None:
            return Response(
                {"status": "ignored", "reason": "Unknown GitHub installation."},
                status=status.HTTP_202_ACCEPTED,
            )

        signature_header = request.headers.get("X-Hub-Signature-256", "")
        if not github_webhook_signature_is_valid(
            raw_body=raw_body,
            signature_header=signature_header,
            provider_config=provider_config,
        ):
            return Response(
                {"detail": "Invalid GitHub webhook signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        event_payload = {
            **payload,
            "_event_name": request.headers.get("X-GitHub-Event", ""),
        }
        result = handle_github_webhook(
            provider_config=provider_config,
            payload=event_payload,
        )
        return Response(result)
