from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from support_requests.models import (
    SupportDestination,
    SupportEscalation,
    SupportMessage,
    SupportProviderConfig,
    SupportRequest,
)
from support_requests.services import (
    append_request_message,
    collect_request_attachment_options,
    create_request_attachment,
    escalate_support_request,
)
from tests.helpers import create_user

pytestmark = pytest.mark.django_db


@override_settings(
    SUPPORT_REQUESTS_EXTRA_ATTACHMENT_PROVIDERS=[
        "tests.helpers.extra_attachment_provider",
    ]
)
def test_collect_request_attachment_options_includes_extra_provider() -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    package_attachment = create_request_attachment(
        request=support_request,
        actor=user,
        uploaded_file=SimpleUploadedFile(
            "screenshot.png",
            b"fake-image-bytes",
            content_type="image/png",
        ),
        kind="screenshot",
    )

    attachments = collect_request_attachment_options(support_request)

    assert {attachment.key for attachment in attachments} == {
        f"package:{package_attachment.pk}",
        "extra:diagnostics",
    }


@override_settings(
    SUPPORT_REQUESTS_PROVIDER_BACKENDS={
        "dummy": "tests.helpers.DummySupportProvider",
    }
)
def test_escalate_support_request_creates_escalation_and_internal_message() -> None:
    user = create_user(email="user@example.com")
    staff_user = create_user(email="staff@example.com", is_staff=True)
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    attachment = create_request_attachment(
        request=support_request,
        actor=user,
        uploaded_file=SimpleUploadedFile(
            "screenshot.png",
            b"fake-image-bytes",
            content_type="image/png",
        ),
        kind="screenshot",
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

    escalation = escalate_support_request(
        request=support_request,
        destination=destination,
        actor=staff_user,
        selected_attachment_keys=[f"package:{attachment.pk}"],
    )

    assert escalation.status == SupportEscalation.Status.OPENED
    assert escalation.remote_issue_number == "101"
    assert support_request.escalations.count() == 1
    assert support_request.messages.filter(is_internal=True).count() == 1
    support_request.refresh_from_db()
    assert support_request.status == SupportRequest.Status.PENDING


def test_escalate_support_request_rejects_unknown_attachment_selection() -> None:
    user = create_user(email="user@example.com")
    staff_user = create_user(email="staff@example.com", is_staff=True)
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

    with override_settings(
        SUPPORT_REQUESTS_PROVIDER_BACKENDS={
            "dummy": "tests.helpers.DummySupportProvider",
        }
    ), pytest.raises(Exception, match="Unknown attachment selection"):
        escalate_support_request(
            request=support_request,
            destination=destination,
            actor=staff_user,
            selected_attachment_keys=["package:missing"],
        )


def test_provider_and_destination_slugs_generate_automatically() -> None:
    provider = SupportProviderConfig.objects.create(
        name="GitHub Production Support",
        backend_key="github",
        auth_mode=SupportProviderConfig.AuthMode.API_TOKEN,
        api_token="secret",
    )
    destination = SupportDestination.objects.create(
        provider=provider,
        name="Aspenroute Backend Issues",
        remote_project="solo-host/aspenroute-backend",
    )

    assert provider.slug == "github-production-support"
    assert destination.slug == "aspenroute-backend-issues"


@override_settings(
    SUPPORT_REQUESTS_PROVIDER_BACKENDS={
        "dummy": "tests.helpers.DummySupportProvider",
    }
)
def test_append_request_message_forwards_user_reply_to_open_escalation() -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    provider = SupportProviderConfig.objects.create(
        name="Dummy provider",
        backend_key="dummy",
        auth_mode=SupportProviderConfig.AuthMode.API_TOKEN,
        api_token="secret",
    )
    destination = SupportDestination.objects.create(
        provider=provider,
        name="Dummy destination",
        remote_project="solo-host/demo",
    )
    SupportEscalation.objects.create(
        request=support_request,
        destination=destination,
        status=SupportEscalation.Status.OPENED,
        remote_issue_id="9001",
        remote_issue_number="101",
        remote_issue_url="https://example.com/solo-host/demo/issues/101",
        title_snapshot=support_request.subject,
        body_snapshot=support_request.body,
    )

    message = append_request_message(
        request=support_request,
        actor=user,
        body="Here is an update from the user.",
    )

    assert message.author_role == SupportMessage.AuthorRole.USER
    assert message.external_message_id.startswith("comment-")
