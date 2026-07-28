from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
    SupportRequestAttachment,
)
from support_requests.services import (
    collect_request_attachment_options,
    escalate_support_request,
)


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
            "slug",
            "name",
            "backend_key",
            "auth_mode",
            "base_url",
            "api_token",
            "github_app_id",
            "github_installation_id",
            "github_private_key",
            "configuration",
            "is_active",
        )
        widgets = {
            "api_token": forms.PasswordInput(render_value=True),
            "github_private_key": forms.Textarea(attrs={"rows": 12}),
        }


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
    search_fields = ("subject", "body", "requester__email")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "resolved_at",
        "last_requester_activity_at",
        "last_staff_activity_at",
        "last_escalated_at",
        "open_issue_link",
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
        url = reverse("admin:support_requests_supportrequest_open_issue", args=[obj.pk])
        return format_html('<a class="button" href="{}">Open remote issue</a>', url)

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
        form = EscalateSupportRequestForm(
            request.POST or None,
            support_request=support_request,
        )
        if request.method == "POST" and form.is_valid():
            escalation = escalate_support_request(
                request=support_request,
                destination=form.cleaned_data["destination"],
                actor=request.user,
                selected_attachment_keys=form.cleaned_data.get("attachments", []),
            )
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
    search_fields = ("body", "request__subject", "author__email")
    readonly_fields = ("id", "created_at", "updated_at")


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
    search_fields = ("request__subject", "uploaded_by__email", "original_filename")
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
                "fields": ("name", "slug", "backend_key", "is_active", "base_url"),
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
                ),
                "description": (
                    "For GitHub, select GitHub App auth and paste the App ID, installation ID, "
                    "and PEM private key from the installed GitHub App."
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
        "request__subject",
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
