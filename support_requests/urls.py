from __future__ import annotations

from rest_framework.routers import DefaultRouter

from support_requests.views import SupportRequestViewSet

app_name = "support_requests"

router = DefaultRouter()
router.register("tickets", SupportRequestViewSet, basename="support-request")

urlpatterns = router.urls
