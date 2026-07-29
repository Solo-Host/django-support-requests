# django-support-requests

Reusable Django app for local support requests, request attachments, and
admin-managed escalation to external issue platforms.

## Features

- local support request records owned by authenticated Django users
- threaded request messages with user/support/system roles
- generic request attachments for screenshots, log bundles, and other files
- pluggable outbound provider layer with GitHub included first
- multiple configured providers and multiple configured remote destinations
- Django admin workflow for reviewing a request, replying locally, and opening a remote issue
- GitHub App webhook sync for remote support replies and issue state changes
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

## API usage

The package exposes a ticket-style API under the mounted prefix.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/tickets/` | List the authenticated user's support requests |
| `POST` | `/tickets/` | Create a local support request |
| `GET` | `/tickets/{id}/` | Retrieve one local support request |
| `GET` | `/tickets/{id}/messages/` | List the non-internal conversation thread, including the opening request body |
| `POST` | `/tickets/{id}/messages/` | Append a new user-visible message |
| `GET` | `/tickets/{id}/attachments/` | List request attachments |
| `POST` | `/tickets/{id}/attachments/` | Attach a file such as a screenshot or log bundle |

### Create a request

JSON-only request:

```http
POST /api/v1/support/tickets/
Content-Type: application/json
Authorization: Bearer <token>

{
  "subject": "Trip classification looks wrong",
  "body": "The app marked a business drive as personal."
}
```

Multipart request with attachment files:

```http
POST /api/v1/support/tickets/
Content-Type: multipart/form-data
Authorization: Bearer <token>

subject=Trip classification looks wrong
body=The screenshot shows the incorrect classification.
attachment_files=<screenshot.png>
attachment_files=<trip-log.zip>
```

### Add a message

```http
POST /api/v1/support/tickets/{id}/messages/
Content-Type: application/json
Authorization: Bearer <token>

{
  "body": "Here is the extra context you asked for."
}
```

### Attach a file later

```http
POST /api/v1/support/tickets/{id}/attachments/
Content-Type: multipart/form-data
Authorization: Bearer <token>

kind=screenshot
file=<updated-screenshot.png>
```

The package keeps the support request local as the system of record. Remote
platform issues are created later by staff from Django admin. The opening
request body is treated as the first end-user-visible message in the local
thread, so both `GET /tickets/{id}/` and `GET /tickets/{id}/messages/` return a
complete conversation view. Once a request has been escalated, new local user
replies are forwarded to the remote issue, and GitHub App webhooks can sync
remote support replies back into the local message thread.

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
package, such as diagnostics log archives.

### Auto-generated slugs

`SupportProviderConfig.slug` and `SupportDestination.slug` are generated
automatically from the `name` field. Django admin does not require operators to
enter them manually.

### Attachment limits

```python
SUPPORT_REQUESTS = {
    "max_attachment_size_mb": 10,
    "max_attachments_per_request": 8,
}
```

## GitHub App setup

`django-support-requests` supports **GitHub App** authentication for GitHub
issue creation. This is the preferred setup. Personal access tokens remain
available only as an explicit fallback.

### 1. Create the GitHub App

In GitHub:

1. Go to **Settings → Developer settings → GitHub Apps**.
2. Create a new GitHub App.
3. Under **Repository permissions**, grant:
   - **Issues: Read and write**
   - **Metadata: Read-only**
4. In the GitHub App's **General** settings page — not in an individual
   repository's **Settings → Webhooks** page — configure the app webhook:
   - mark the webhook **Active**
   - set the **Webhook URL** to your mounted support requests webhook endpoint
   - set the **Webhook secret**
   - leave **SSL verification** enabled unless you have a very specific local
     development reason not to

   ```text
   https://<your-host>/api/v1/support/webhooks/github/
   ```

   Repository-level webhooks are **not** the webhook mechanism used by this
   package's GitHub App integration.

5. In the GitHub App's **Permissions & events** settings page, grant the
   repository permissions that allow the webhook events this package uses:
   - **Issues: Read and write**
   - **Metadata: Read-only**

   On GitHub's current UI, you may not see a separate repo-style "subscribe"
   screen on the webhook form itself. The important part is that this is still a
   **GitHub App webhook**, and the `issues` / `issue_comment` deliveries are
   governed by the app's permissions/events configuration rather than by a
   repository-level webhook.

6. Generate a **private key** for the app and download the PEM file.

### 2. Install the app on the target repositories

1. Install the GitHub App on the user or organization that owns the support
   repositories.
2. Limit the installation to the repos that should receive support issues, or
   install it broadly if that matches your workflow.
3. Record the **Installation ID** for the installation that can see the target
   repositories.

### 3. Configure the provider in `django-support-requests`

Create a `SupportProviderConfig` record in Django admin with:

| Field | Value |
| --- | --- |
| `backend_key` | `github` |
| `auth_mode` | `GitHub App` |
| `base_url` | blank for github.com, or your GitHub Enterprise API URL |
| `github_app_id` | the App ID from the GitHub App settings page |
| `github_installation_id` | the installation ID that can access the target repos |
| `github_private_key` | the full PEM private key contents |
| `github_webhook_secret` | the GitHub App webhook secret |
| `api_token` | leave blank when using GitHub App auth |

The GitHub provider will:

1. sign a short-lived app JWT with the private key
2. exchange that JWT for an installation access token
3. use the installation token to create the issue in the configured repo
4. verify inbound GitHub webhook deliveries with the webhook secret

### 4. Configure one destination per repo

Create a `SupportDestination` row for each repo that staff may escalate into:

| Field | Example |
| --- | --- |
| `provider` | `GitHub production support` |
| `name` | `Aspenroute backend issues` |
| `remote_project` | `Solo-Host/aspenroute-backend` |
| `default_labels` | `["support", "triage"]` |

Each destination maps one admin-selectable support target. If you need multiple
repos, create multiple destination rows.

### 5. Open the remote issue from Django admin

1. Review the local support request in Django admin.
2. Use **Reply to requester** to add a local support response, or **Open remote
   issue** to escalate it externally.
3. If you are escalating, choose the GitHub destination.
4. Choose which attachments to forward.
5. Submit the escalation.

The standalone support-message admin workflow also uses the support-request
lookup/search flow, so staff can find the correct ticket by ticket ID,
requester email, subject, or description instead of relying on the subject line
alone.

For GitHub, attachments are forwarded as links in the issue body because the
official GitHub issue API does not accept direct binary uploads.

### 6. What webhook sync does

With the GitHub App webhook configured:

- `issue_comment.created` syncs remote support replies into the local support
  request message thread
- `issues.closed` marks the local request as resolved
- `issues.reopened` reopens the local request
- new local user replies are posted back to the linked GitHub issue as comments

Those synced remote support replies are stored as **non-internal** support
messages, so they are returned by `GET /tickets/{id}/messages/` as part of the
same end-user-visible thread that starts with the opening request body.

### Optional fallback: personal access token

If you must use a PAT temporarily:

1. set `auth_mode` to `API token`
2. paste the token into `api_token`
3. leave the GitHub App fields blank

GitHub App auth is the preferred path and is the documented/default setup for
this package.

## Development

```bash
uv sync --extra dev
uv run tox
uv run tox -e py313
uv run tox -e lint
uv run tox -e mypy
uv run tox -e security
```
