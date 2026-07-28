from __future__ import annotations

from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, override_settings
from django.urls import reverse

from support_requests.admin import SupportRequestAdmin
from support_requests.models import (
    SupportDestination,
    SupportEscalation,
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
