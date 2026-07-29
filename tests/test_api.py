from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from support_requests.models import SupportMessage, SupportRequest, SupportRequestAttachment
from tests.helpers import create_user


@pytest.mark.django_db
def test_requests_require_auth(api_client: APIClient) -> None:
    assert api_client.get("/api/support/tickets/").status_code == 403


@pytest.mark.django_db
def test_request_create_requester_scoped(api_client: APIClient) -> None:
    alice = create_user(email="alice@example.com")
    bob = create_user(email="bob@example.com")
    SupportRequest.objects.create(requester=bob, subject="Bob", body="Only Bob sees this.")
    api_client.force_authenticate(user=alice)

    response = api_client.post(
        "/api/support/tickets/",
        {"subject": "Hi", "body": "Hello"},
        format="json",
    )

    assert response.status_code == 201, response.content
    list_response = api_client.get("/api/support/tickets/")
    payload = list_response.json()
    count = payload["count"] if isinstance(payload, dict) and "count" in payload else len(payload)
    assert count == 1


@pytest.mark.django_db
def test_request_add_message(api_client: APIClient) -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f"/api/support/tickets/{support_request.id}/messages/",
        {"body": "More info"},
        format="json",
    )

    assert response.status_code == 201, response.content
    assert SupportMessage.objects.filter(request=support_request, body="More info").count() == 1


@pytest.mark.django_db
def test_request_detail_includes_opening_message_in_thread(api_client: APIClient) -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    api_client.force_authenticate(user=user)

    response = api_client.get(f"/api/support/tickets/{support_request.id}/")

    assert response.status_code == 200, response.content
    assert response.json()["messages"][0]["id"] == f"opening-{support_request.id}"
    assert response.json()["messages"][0]["body"] == support_request.body
    assert response.json()["messages"][0]["author_role"] == SupportMessage.AuthorRole.USER


@pytest.mark.django_db
def test_request_messages_include_opening_message_and_hide_internal_notes(
    api_client: APIClient,
) -> None:
    user = create_user(email="user@example.com")
    staff_user = create_user(email="staff@example.com", is_staff=True)
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    SupportMessage.objects.create(
        request=support_request,
        author=user,
        author_role=SupportMessage.AuthorRole.USER,
        body="User-visible update",
        is_internal=False,
    )
    SupportMessage.objects.create(
        request=support_request,
        author=staff_user,
        author_role=SupportMessage.AuthorRole.SUPPORT,
        body="Support reply",
        is_internal=False,
    )
    SupportMessage.objects.create(
        request=support_request,
        author=staff_user,
        author_role=SupportMessage.AuthorRole.SUPPORT,
        body="Internal note",
        is_internal=True,
    )
    api_client.force_authenticate(user=user)

    response = api_client.get(f"/api/support/tickets/{support_request.id}/messages/")

    assert response.status_code == 200, response.content
    assert [message["body"] for message in response.json()] == [
        support_request.body,
        "User-visible update",
        "Support reply",
    ]


@pytest.mark.django_db
def test_request_create_accepts_attachment_files(api_client: APIClient) -> None:
    user = create_user(email="user@example.com")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        "/api/support/tickets/",
        {
            "subject": "Screenshot issue",
            "body": "See the attached screenshot.",
            "attachment_files": [
                SimpleUploadedFile(
                    "screenshot.png",
                    b"fake-image-bytes",
                    content_type="image/png",
                )
            ],
        },
        format="multipart",
    )

    assert response.status_code == 201, response.content
    assert SupportRequestAttachment.objects.filter(request_id=response.json()["id"]).count() == 1
    assert response.json()["attachments"][0]["original_filename"] == "screenshot.png"


@pytest.mark.django_db
def test_request_attachment_upload_action(api_client: APIClient) -> None:
    user = create_user(email="user@example.com")
    support_request = SupportRequest.objects.create(
        requester=user,
        subject="Need help",
        body="Initial message",
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f"/api/support/tickets/{support_request.id}/attachments/",
        {
            "kind": "screenshot",
            "file": SimpleUploadedFile(
                "issue.png",
                b"fake-image-bytes",
                content_type="image/png",
            ),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.content
    assert SupportRequestAttachment.objects.filter(request=support_request).count() == 1
    assert response.json()["kind"] == "screenshot"
