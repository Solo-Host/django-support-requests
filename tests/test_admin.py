from __future__ import annotations

from typing import Any, cast

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, override_settings
from django.urls import reverse

from support_requests.admin import SupportMessageAdmin, SupportRequestAdmin
from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
)
from tests.helpers import create_user

pytestmark = pytest.mark.django_db


def test_support_request_admin_exposes_open_issue_link() -> None:
    admin = SupportRequestAdmin(SupportRequest, AdminSite())
    support_request = SupportRequest.objects.create(
        requester=create_user(email="user@example.com"),
        subject="Need help",
        body="Initial message",
    )

    link = str(admin.open_issue_link(support_request))

    assert (
        reverse("admin:support_requests_supportrequest_open_issue", args=[support_request.pk])
        in link
    )


def test_support_request_admin_exposes_reply_to_requester_link() -> None:
    admin = SupportRequestAdmin(SupportRequest, AdminSite())
    support_request = SupportRequest.objects.create(
        requester=create_user(email="user@example.com"),
        subject="Need help",
        body="Initial message",
    )

    link = str(admin.reply_to_requester_link(support_request))

    assert reverse("admin:support_requests_supportmessage_add") in link
    assert f"request={support_request.pk}" in link
    assert f"_request={support_request.pk}" in link


def test_support_request_admin_hides_open_issue_link_for_unsaved_request() -> None:
    admin = SupportRequestAdmin(SupportRequest, AdminSite())
    request_factory = RequestFactory()
    request = SupportRequest(subject="", body="")

    assert "open_issue_link" not in admin.get_readonly_fields(
        request_factory.get("/admin/support_requests/supportrequest/add/"),
        None,
    )
    assert "Save a requester, subject, and body before opening a remote issue." in str(
        admin.open_issue_link(request)
    )
    assert "reply_to_requester_link" not in admin.get_readonly_fields(
        request_factory.get("/admin/support_requests/supportrequest/add/"),
        None,
    )


@override_settings(
    SUPPORT_REQUESTS_PROVIDER_BACKENDS={
        "dummy": "tests.helpers.DummySupportProvider",
    }
)
def test_support_request_admin_open_issue_view_creates_escalation(client: Any) -> None:
    user = create_user(email="user@example.com")
    staff_user = create_user(
        email="staff@example.com",
        is_staff=True,
        is_superuser=True,
    )
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    provider = SupportProviderConfig.objects.create(
        slug="dummy-provider",
        name="Dummy provider",
        backend_key="dummy",
        api_token="secret",
    )
    destination = SupportDestination.objects.create(
        provider=provider,
        slug="dummy-destination",
        name="Dummy destination",
        remote_project="solo-host/demo",
    )
    client.force_login(staff_user)

    response = client.post(
        reverse("admin:support_requests_supportrequest_open_issue", args=[support_request.pk]),
        {"destination": str(destination.pk), "attachments": []},
    )

    assert response.status_code == 302
    assert SupportEscalation.objects.filter(request=support_request).count() == 1


def test_support_request_admin_open_issue_view_rejects_invalid_request(client: Any) -> None:
    staff_user = create_user(
        email="staff@example.com",
        is_staff=True,
        is_superuser=True,
    )
    support_request = SupportRequest.objects.create(
        requester=create_user(email="user@example.com"),
        subject=" ",
        body=" ",
    )
    client.force_login(staff_user)

    response = client.get(
        reverse("admin:support_requests_supportrequest_open_issue", args=[support_request.pk]),
    )

    assert response.status_code == 302
    assert SupportEscalation.objects.filter(request=support_request).count() == 0


def test_support_message_admin_uses_request_lookup_and_explicit_search_fields() -> None:
    admin = SupportMessageAdmin(SupportMessage, AdminSite())

    assert admin.raw_id_fields == ("request",)
    assert "request__id" in admin.search_fields
    assert "request__requester__email" in admin.search_fields


def test_support_message_admin_save_model_creates_support_reply_and_updates_activity() -> None:
    admin = SupportMessageAdmin(SupportMessage, AdminSite())
    requester = create_user(email="user@example.com")
    staff_user = create_user(
        email="staff@example.com",
        is_staff=True,
        is_superuser=True,
    )
    support_request = SupportRequest.objects.create(
        requester=requester,
        subject="Need help",
        body="Initial message",
    )
    request = RequestFactory().post("/admin/support_requests/supportmessage/add/")
    cast(Any, request).user = staff_user
    form_class = admin.get_form(request)
    form = form_class(
        data={
            "request": str(support_request.pk),
            "body": "We are investigating the issue now.",
            "is_internal": False,
        }
    )

    assert form.is_valid(), form.errors

    admin.save_model(request, form.save(commit=False), cast(Any, form), change=False)
    support_request.refresh_from_db()

    reply = SupportMessage.objects.get(
        request=support_request,
        body="We are investigating the issue now.",
    )
    assert reply.author_id == staff_user.id
    assert reply.author_role == SupportMessage.AuthorRole.SUPPORT
    assert support_request.last_staff_activity_at is not None
