from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from rest_framework import serializers

from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
    SupportRequestAttachment,
)
from support_requests.services import (
    append_request_message,
    collect_request_attachment_options,
    escalate_support_request,
    request_can_be_escalated,
    request_escalation_errors,
)


def _error_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


class EscalateSupportRequestForm(forms.Form):
    destination = forms.ModelChoiceField(
        queryset=SupportDestination.objects.none(),
        required=True,
    )
    attachments = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args: Any, support_request: SupportRequest, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        destination_field = cast(forms.ModelChoiceField, self.fields["destination"])
        destination_field.queryset = SupportDestination.objects.filter(
            is_active=True,
            provider__is_active=True,
        ).select_related("provider")
        attachment_options = collect_request_attachment_options(support_request)
        attachments_field = cast(forms.MultipleChoiceField, self.fields["attachments"])
        attachments_field.choices = [
            (
                attachment.key,
                f"{attachment.display_name} ({attachment.kind})",
            )
            for attachment in attachment_options
        ]
        self.initial.setdefault(
            "attachments",
            [attachment.key for attachment in attachment_options],
        )


class SupportProviderConfigAdminForm(forms.ModelForm):
    class Meta:
        model = SupportProviderConfig
        fields = (
            "name",
            "backend_key",
            "auth_mode",
            "base_url",
            "api_token",
            "github_app_id",
            "github_installation_id",
            "github_private_key",
            "github_webhook_secret",
            "configuration",
            "is_active",
        )
        widgets = {
            "api_token": forms.PasswordInput(render_value=True),
            "github_private_key": forms.Textarea(attrs={"rows": 12}),
            "github_webhook_secret": forms.PasswordInput(render_value=True),
        }


class SupportDestinationAdminForm(forms.ModelForm):
    class Meta:
        model = SupportDestination
        fields = (
            "provider",
            "name",
            "remote_project",
            "default_labels",
            "configuration",
            "is_active",
        )


class SupportRequestAttachmentInline(admin.TabularInline):
    model = SupportRequestAttachment
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "kind",
        "original_filename",
        "file_size",
        "content_type",
        "file_link",
    )
    readonly_fields = fields

    @admin.display(description="File")
    def file_link(self, obj: SupportRequestAttachment) -> str:
        return format_html('<a href="{}">{}</a>', obj.file.url, obj.original_filename)

    def has_add_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        del request, obj
        return False


class SupportEscalationInline(admin.TabularInline):
    model = SupportEscalation
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "destination",
        "status",
        "remote_issue_number",
        "remote_issue_link",
    )
    readonly_fields = fields

    @admin.display(description="Remote issue")
    def remote_issue_link(self, obj: SupportEscalation) -> str:
        if not obj.remote_issue_url:
            return "—"
        label = obj.remote_issue_number or obj.remote_issue_url
        return format_html('<a href="{}">{}</a>', obj.remote_issue_url, label)

    def has_add_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        del request, obj
        return False


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "requester",
        "status",
        "priority",
        "category",
        "attachment_count",
        "escalation_count",
        "created_at",
        "last_requester_activity_at",
        "last_staff_activity_at",
        "last_escalated_at",
    )
    list_filter = ("status", "priority", "category")
    search_fields = ("id", "subject", "body", "requester__email")
    search_help_text = "Search by ticket ID, requester email, subject, or description."
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "resolved_at",
        "last_requester_activity_at",
        "last_staff_activity_at",
        "last_escalated_at",
        "open_issue_link",
        "reply_to_requester_link",
    )
    inlines = (SupportRequestAttachmentInline, SupportEscalationInline)

    @admin.display(description="Attachments")
    def attachment_count(self, obj: SupportRequest) -> int:
        return obj.attachments.count()

    @admin.display(description="Escalations")
    def escalation_count(self, obj: SupportRequest) -> int:
        return obj.escalations.count()

    @admin.display(description="Remote issue")
    def open_issue_link(self, obj: SupportRequest) -> str:
        if not request_can_be_escalated(obj):
            return "Save a requester, subject, and body before opening a remote issue."
        url = reverse("admin:support_requests_supportrequest_open_issue", args=[obj.pk])
        return format_html('<a class="button" href="{}">Open remote issue</a>', url)

    @admin.display(description="Reply")
    def reply_to_requester_link(self, obj: SupportRequest) -> str:
        query = urlencode(
            {
                "request": str(obj.pk),
                "_request": str(obj.pk),
            }
        )
        url = f"{reverse('admin:support_requests_supportmessage_add')}?{query}"
        return format_html('<a class="button" href="{}">Reply to requester</a>', url)

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: SupportRequest | None = None,
    ) -> tuple[str, ...]:
        readonly_fields = tuple(super().get_readonly_fields(request, obj))
        if obj is None:
            hidden_fields = {"open_issue_link", "reply_to_requester_link"}
            return tuple(field for field in readonly_fields if field not in hidden_fields)
        return readonly_fields

    def get_urls(self) -> list[Any]:
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/open-issue/",
                self.admin_site.admin_view(self.open_issue_view),
                name="support_requests_supportrequest_open_issue",
            ),
        ]
        return custom_urls + urls

    def open_issue_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        support_request = get_object_or_404(
            SupportRequest.objects.prefetch_related("attachments"),
            pk=object_id,
        )
        if errors := request_escalation_errors(support_request):
            self.message_user(
                request,
                " ".join(errors),
                level=messages.ERROR,
            )
            change_url = reverse(
                "admin:support_requests_supportrequest_change",
                args=[support_request.pk],
            )
            return HttpResponseRedirect(change_url)
        form = EscalateSupportRequestForm(
            request.POST or None,
            support_request=support_request,
        )
        if request.method == "POST" and form.is_valid():
            try:
                escalation = escalate_support_request(
                    request=support_request,
                    destination=form.cleaned_data["destination"],
                    actor=request.user,
                    selected_attachment_keys=form.cleaned_data.get("attachments", []),
                )
            except serializers.ValidationError as exc:
                detail = exc.detail
                if isinstance(detail, dict):
                    for field_name, field_errors in detail.items():
                        field = None if field_name == "non_field_errors" else field_name
                        for error in _error_strings(field_errors):
                            form.add_error(field, str(error))
                elif isinstance(detail, list):
                    for error in _error_strings(detail):
                        form.add_error(None, str(error))
                else:
                    form.add_error(None, str(detail))
            else:
                self.message_user(
                    request,
                    (
                        "Opened remote issue "
                        f"{escalation.remote_issue_number or escalation.remote_issue_id}."
                    ),
                    level=messages.SUCCESS,
                )
                change_url = reverse(
                    "admin:support_requests_supportrequest_change",
                    args=[support_request.pk],
                )
                return HttpResponseRedirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": support_request,
            "title": f"Open remote issue for {support_request.subject}",
            "form": form,
        }
        return render(request, "admin/support_requests/open_issue.html", context)


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("request", "author", "author_role", "is_internal", "created_at")
    list_filter = ("author_role", "is_internal")
    search_fields = (
        "body",
        "request__id",
        "request__subject",
        "request__requester__email",
        "author__email",
        "external_message_id",
    )
    search_help_text = "Search by ticket ID, requester email, subject, or message text."
    raw_id_fields = ("request",)
    readonly_fields = (
        "id",
        "request_link",
        "author",
        "author_role",
        "external_message_id",
        "body",
        "is_internal",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Request")
    def request_link(self, obj: SupportMessage) -> str:
        url = reverse("admin:support_requests_supportrequest_change", args=[obj.request_id])
        return format_html('<a href="{}">{}</a>', url, obj.request)

    def get_fields(
        self,
        request: HttpRequest,
        obj: SupportMessage | None = None,
    ) -> tuple[str, ...]:
        if obj is None:
            return ("request", "body", "is_internal")
        return (
            "request_link",
            "author",
            "author_role",
            "external_message_id",
            "body",
            "is_internal",
            "created_at",
            "updated_at",
        )

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: SupportMessage | None = None,
    ) -> tuple[str, ...]:
        if obj is None:
            return ()
        return tuple(super().get_readonly_fields(request, obj))

    def save_model(
        self,
        request: HttpRequest,
        obj: SupportMessage,
        form: forms.ModelForm[Any],
        change: bool,
    ) -> None:
        if change:
            super().save_model(request, obj, form, change)
            return

        support_request = cast(SupportRequest, form.cleaned_data["request"])
        body = cast(str, form.cleaned_data["body"])
        is_internal = bool(form.cleaned_data["is_internal"])
        message = append_request_message(
            request=support_request,
            actor=request.user,
            body=body,
            is_internal=is_internal,
            author_role=SupportMessage.AuthorRole.SUPPORT,
            propagate_to_remote=False,
        )
        obj.pk = message.pk
        obj.request = message.request
        obj.author = message.author
        obj.author_role = message.author_role
        obj.external_message_id = message.external_message_id
        obj.body = message.body
        obj.is_internal = message.is_internal
        obj.created_at = message.created_at
        obj.updated_at = message.updated_at

    def response_add(
        self,
        request: HttpRequest,
        obj: SupportMessage,
        post_url_continue: str | None = None,
    ) -> HttpResponse:
        response = super().response_add(request, obj, post_url_continue)
        request_id = request.GET.get("_request")
        if request_id:
            return HttpResponseRedirect(
                reverse("admin:support_requests_supportrequest_change", args=[request_id])
            )
        return response


@admin.register(SupportRequestAttachment)
class SupportRequestAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "request",
        "uploaded_by",
        "kind",
        "original_filename",
        "file_link",
        "file_size",
        "created_at",
    )
    list_filter = ("kind",)
    search_fields = (
        "request__id",
        "request__subject",
        "request__requester__email",
        "uploaded_by__email",
        "original_filename",
    )
    readonly_fields = ("id", "request_link", "file_link", "created_at", "updated_at")

    @admin.display(description="Request")
    def request_link(self, obj: SupportRequestAttachment) -> str:
        url = reverse("admin:support_requests_supportrequest_change", args=[obj.request_id])
        return format_html('<a href="{}">{}</a>', url, obj.request)

    @admin.display(description="File")
    def file_link(self, obj: SupportRequestAttachment) -> str:
        return format_html('<a href="{}">{}</a>', obj.file.url, obj.original_filename)


@admin.register(SupportProviderConfig)
class SupportProviderConfigAdmin(admin.ModelAdmin):
    form = SupportProviderConfigAdminForm
    list_display = ("name", "slug", "backend_key", "is_active", "created_at")
    list_filter = ("backend_key", "is_active")
    search_fields = ("name", "slug", "backend_key")
    fieldsets = (
        (
            "Provider basics",
            {
                "fields": ("name", "backend_key", "is_active", "base_url"),
                "description": (
                    "Use the GitHub backend with GitHub App authentication whenever possible. "
                    "Leave base_url blank for github.com, or set it to the GitHub Enterprise "
                    "API root."
                ),
            },
        ),
        (
            "GitHub App authentication (preferred)",
            {
                "fields": (
                    "auth_mode",
                    "github_app_id",
                    "github_installation_id",
                    "github_private_key",
                    "github_webhook_secret",
                ),
                "description": (
                    "For GitHub, select GitHub App auth and paste the App ID, installation ID, "
                    "PEM private key, and webhook secret from the installed GitHub App."
                ),
            },
        ),
        (
            "Legacy token fallback",
            {
                "fields": ("api_token",),
                "description": (
                    "Use only when you intentionally need a personal access token or pre-minted "
                    "API token instead of GitHub App authentication."
                ),
            },
        ),
        (
            "Advanced configuration",
            {
                "fields": ("configuration",),
            },
        ),
    )


@admin.register(SupportDestination)
class SupportDestinationAdmin(admin.ModelAdmin):
    form = SupportDestinationAdminForm
    list_display = ("name", "slug", "provider", "remote_project", "is_active")
    list_filter = ("provider", "is_active")
    search_fields = ("name", "slug", "remote_project")


@admin.register(SupportEscalation)
class SupportEscalationAdmin(admin.ModelAdmin):
    list_display = (
        "request",
        "destination",
        "status",
        "remote_issue_number",
        "remote_issue_link",
        "created_at",
    )
    list_filter = ("status", "destination")
    search_fields = (
        "request__id",
        "request__subject",
        "request__requester__email",
        "destination__name",
        "remote_issue_number",
        "remote_issue_url",
    )
    readonly_fields = (
        "id",
        "request_link",
        "remote_issue_link",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Request")
    def request_link(self, obj: SupportEscalation) -> str:
        url = reverse("admin:support_requests_supportrequest_change", args=[obj.request_id])
        return format_html('<a href="{}">{}</a>', url, obj.request)

    @admin.display(description="Remote issue")
    def remote_issue_link(self, obj: SupportEscalation) -> str:
        if not obj.remote_issue_url:
            return "—"
        label = obj.remote_issue_number or obj.remote_issue_url
        return format_html('<a href="{}">{}</a>', obj.remote_issue_url, label)
