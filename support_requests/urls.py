from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from support_requests.views import GitHubWebhookView, SupportRequestViewSet

app_name = "support_requests"

router = DefaultRouter()
router.register("tickets", SupportRequestViewSet, basename="support-request")

urlpatterns = [
    path("webhooks/github/", GitHubWebhookView.as_view(), name="support-webhook-github"),
    *router.urls,
]
