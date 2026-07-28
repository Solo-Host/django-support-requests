# Copilot Instructions for django-support-requests

## Quick Start

This is a reusable Django package for local support requests, request
attachments, and admin-managed escalation to external issue platforms. Use `uv`
for dependency management and `tox` as the canonical local validation entry
point. This repository currently targets Python 3.13 only.

```bash
uv sync --extra dev
```

`uv.lock` is committed. Update it when dependency metadata changes, and keep CI
compatible with `uv sync --frozen --extra dev`.

## Build, Test, and Lint Commands

### Setup and Packaging
```bash
uv sync --extra dev
uv run python -m build
```

### Tox Entry Points
```bash
uv run tox
uv run tox -e py313
uv run tox -e lint
uv run tox -e mypy
uv run tox -e security
uv run tox -e py313 -- tests/test_api.py
```

### Direct Commands
```bash
uv run pytest
uv run ruff check support_requests tests
uv run mypy support_requests tests
uv run bandit -q -r support_requests -x support_requests/migrations
uv run pip-audit
```

`tox` is the canonical entry point for local and CI checks. The configured
environments are `py313`, `lint`, `mypy`, and `security`, with optional `ruff`,
`bandit`, and `pip-audit` aliases for focused runs.

## High-Level Architecture

### Core Components

**Models and admin** (`support_requests/models.py`, `support_requests/admin.py`)
- `SupportRequest` is the local system-of-record ticket
- `SupportMessage` stores the local request conversation
- `SupportRequestAttachment` stores generic request files
- `SupportProviderConfig`, `SupportDestination`, and `SupportEscalation`
  configure and track outbound provider integrations
- Django admin lets staff review requests and open remote issues

**Services and providers** (`support_requests/services.py`, `support_requests/providers/`)
- The service layer handles request messages, attachment collection, and remote
  escalation
- Provider backends translate one local request into platform-specific issue
  creation behavior
- Host projects can register extra attachment providers for project-specific
  files that should accompany an escalation

**API surface** (`support_requests/serializers.py`, `support_requests/views.py`, `support_requests/urls.py`)
- Authenticated DRF endpoints expose request, message, and attachment actions
- Host projects mount `support_requests.urls` under their preferred API prefix

**Configuration** (`support_requests/conf.py`)
- Static Django settings define provider backends, extra attachment providers,
  and attachment limits

## Key Conventions

### Code Style
- Use `from __future__ import annotations` when forward references are needed
- Ruff is the linting tool; line length is 99 characters
- Migration files are exempt from the normal line-length and import-order rules

### Django Patterns
- Keep host-specific issue-routing and attachment sources outside this package
- Keep the provider registry indirect so host apps can add GitLab or other
  backends without editing package internals
- Treat local support requests as the canonical source of truth; remote platform
  issues are escalations, not replacements

### Versioning and Release Flow
- `pyproject.toml` is the source of truth for the package version
- `support_requests/__init__.py` and the editable `uv.lock` package entry must
  stay aligned with `pyproject.toml`
- Normal feature work should not bump the version manually
- Releases go through `.github/workflows/release.yml`, which creates a release
  bump PR, updates `pyproject.toml`, `support_requests/__init__.py`, and the
  committed `uv.lock` metadata, then creates the tag and GitHub Release after
  merge
- The release flow is GitHub-only for now; do not add PyPI publishing steps

## Git Workflow

### Using Worktrees
- Create branch worktrees under the shared `../../worktrees/` directory (full
  path: `/home/bjorn/workspace/web_projects/worktrees/`).
- Do not work directly on `main`; create a branch worktree first:
  ```bash
  git worktree add ../../worktrees/django-support-requests-my-change -b my-change main
  cd ../../worktrees/django-support-requests-my-change
  ```
- Commit and push from that worktree branch as usual.

## Important Notes

- Tests use `tests.settings`
- `support_requests/__init__.py` and `uv.lock` should stay in sync with
  packaging metadata changes
- Keep workflow path filters aligned with this repo's package path
