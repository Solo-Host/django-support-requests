from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


def support_request_attachment_upload_to(instance: models.Model, filename: str) -> str:
    suffix = Path(filename).suffix or ".bin"
    request_id = getattr(instance, "request_id", "request")
    return f"support-requests/attachments/{request_id}/{uuid.uuid4().hex}{suffix}"


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SupportRequest(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class Category(models.TextChoices):
        BUG = "bug", "Bug"
        FEATURE = "feature", "Feature"
        ACCOUNT = "account", "Account"
        BILLING = "billing", "Billing"
        OTHER = "other", "Other"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_requests",
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.OTHER)
    metadata = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    last_requester_activity_at = models.DateTimeField(null=True, blank=True)
    last_staff_activity_at = models.DateTimeField(null=True, blank=True)
    last_escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "support_requests_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["requester", "status"]),
            models.Index(fields=["status", "priority"]),
        ]

    def __str__(self) -> str:
        return self.subject


class SupportMessage(TimeStampedUUIDModel):
    class AuthorRole(models.TextChoices):
        USER = "user", "User"
        SUPPORT = "support", "Support"
        SYSTEM = "system", "System"

    request = models.ForeignKey(
        SupportRequest,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_request_messages",
    )
    author_role = models.CharField(
        max_length=16,
        choices=AuthorRole.choices,
        default=AuthorRole.USER,
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False)

    class Meta:
        db_table = "support_requests_message"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["request", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Message on {self.request_id}"


class SupportRequestAttachment(TimeStampedUUIDModel):
    class Kind(models.TextChoices):
        ATTACHMENT = "attachment", "Attachment"
        SCREENSHOT = "screenshot", "Screenshot"
        LOG_BUNDLE = "log_bundle", "Log bundle"
        OTHER = "other", "Other"

    request = models.ForeignKey(
        SupportRequest,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_request_attachments",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.ATTACHMENT)
    file = models.FileField(upload_to=support_request_attachment_upload_to)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        db_table = "support_requests_attachment"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["request", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.request_id} attachment {self.original_filename}"


class SupportProviderConfig(TimeStampedUUIDModel):
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    backend_key = models.CharField(max_length=50)
    base_url = models.URLField(blank=True, default="")
    api_token = models.CharField(max_length=255, blank=True, default="")
    configuration = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "support_requests_provider_config"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SupportDestination(TimeStampedUUIDModel):
    provider = models.ForeignKey(
        SupportProviderConfig,
        on_delete=models.CASCADE,
        related_name="destinations",
    )
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    remote_project = models.CharField(max_length=255)
    default_labels = models.JSONField(default=list, blank=True)
    configuration = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "support_requests_destination"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["provider", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name


class SupportEscalation(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        OPENED = "opened", "Opened"
        FAILED = "failed", "Failed"

    request = models.ForeignKey(
        SupportRequest,
        on_delete=models.CASCADE,
        related_name="escalations",
    )
    destination = models.ForeignKey(
        SupportDestination,
        on_delete=models.CASCADE,
        related_name="escalations",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_request_escalations",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPENED)
    remote_issue_id = models.CharField(max_length=80, blank=True, default="")
    remote_issue_number = models.CharField(max_length=80, blank=True, default="")
    remote_issue_url = models.URLField(max_length=500, blank=True, default="")
    title_snapshot = models.CharField(max_length=200)
    body_snapshot = models.TextField()
    forwarded_attachments = models.JSONField(default=list, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    failure_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "support_requests_escalation"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["request", "created_at"]),
            models.Index(fields=["destination", "created_at"]),
        ]

    def __str__(self) -> str:
        if self.remote_issue_number:
            return f"{self.request_id} -> {self.remote_issue_number}"
        return f"{self.request_id} escalation"
