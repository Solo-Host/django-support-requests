# django-support-requests

Reusable Django app for local support requests, request attachments, and
admin-managed escalation to external issue platforms.

## Features

- local support request records owned by authenticated Django users
- threaded request messages with user/support/system roles
- generic request attachments for screenshots, log bundles, and other files
- pluggable outbound provider layer with GitHub included first
- multiple configured providers and multiple configured remote destinations
- Django admin workflow for reviewing a request and opening a remote issue
- optional DRF URL set for request, message, and attachment APIs
- host hook for exposing extra attachment sources during escalation

## GitHub attachment behavior

GitHub's official issue API does not support direct binary attachment uploads.
The built-in GitHub provider forwards attachments by including storage-backed
links in the created issue body so screenshots, log bundles, and similar files
still travel with the escalation in a supported way.

## Installation

```bash
uv add git+https://github.com/Solo-Host/django-support-requests.git
```

Add the package to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "rest_framework",
    "support_requests",
]
```

Mount the API wherever it fits your project:

```python
from django.urls import include, path

urlpatterns = [
    path("api/v1/support/", include("support_requests.urls")),
]
```

## Configuration

### Provider backends

`SUPPORT_REQUESTS_PROVIDER_BACKENDS` maps provider keys to dotted-path backend
classes. The default registry ships with GitHub:

```python
SUPPORT_REQUESTS_PROVIDER_BACKENDS = {
    "github": "support_requests.providers.github.GitHubSupportProvider",
}
```

### Extra attachment providers

Host projects can expose additional request-related attachments during admin
escalation by returning `SupportAttachmentLink` values from one or more dotted
path callables:

```python
SUPPORT_REQUESTS_EXTRA_ATTACHMENT_PROVIDERS = [
    "myproject.support.attachments.get_extra_request_attachments",
]
```

This is useful when the host project keeps certain attachment flows outside the
package, such as Aspenroute's diagnostics log archives.

### Attachment limits

```python
SUPPORT_REQUESTS = {
    "max_attachment_size_mb": 10,
    "max_attachments_per_request": 8,
}
```

## Development

```bash
uv sync --extra dev
uv run tox
uv run tox -e py313
uv run tox -e lint
uv run tox -e mypy
uv run tox -e security
```
