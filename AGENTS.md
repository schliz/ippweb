# IPPWeb System Architecture

## Overview
IPPWeb is a web-based printing service that bridges modern OIDC authentication with legacy CUPS printing infrastructure. It allows users to authenticate via Keycloak, upload PDF documents, configure print options, and track job status in real-time.

## Tech Stack
- **Backend Framework**: Flask (Python 3.13+)
- **Database**: SQLite (dev) / PostgreSQL (prod) via SQLAlchemy & Flask-Migrate
- **Authentication**: OIDC via Authlib (Keycloak integration)
- **Printing**: pycups (CUPS bindings)
- **PDF Processing**: pypdf (Page counting), python-magic (MIME type validation)
- **Frontend**: Jinja2 templates, vanilla JavaScript (ES6+), CSS Grid/Flexbox
- **Async/Real-time**: Server-Sent Events (SSE) for job status updates

## Project Structure
```
ippweb/
├── app/
│   ├── routes/          # Blueprint definitions
│   │   ├── auth.py      # OIDC login/callback/logout
│   │   ├── jobs.py      # Dashboard, API, SSE stream
│   │   └── print.py     # Print submission & status pages
│   ├── services/
│   │   └── job_sync.py  # Background thread for CUPS <-> DB sync
│   ├── templates/       # Jinja2 HTML templates
│   ├── static/          # CSS and assets
│   ├── auth.py          # Auth decorators & helpers
│   ├── models.py        # SQLAlchemy models (User, PrintJob)
│   └── cups_client.py   # Wrapper around pycups
├── keycloak/            # Keycloak configuration
├── migrations/          # Alembic database migrations
└── uploads/             # Temporary storage for uploaded PDFs
```

## Key Flows

### 1. Print Submission
1. User uploads a PDF at `/print/<printer>`.
2. Backend validates MIME type and counts pages using `pypdf`.
3. `PrintJob` record created in DB with status `PENDING`.
4. Job submitted to CUPS via `pycups`.
5. CUPS Job ID saved to DB; status updates to `SUBMITTED`.

### 2. Authentication
- Uses **Authlib** with **Keycloak**.
- Users are JIT (Just-In-Time) provisioned in the local database upon first login via the `User.upsert_from_oidc` method.
- Session management handled by Flask-Session (filesystem).

### 3. Job Synchronization
- **JobSyncService** runs a background daemon thread.
- Periodically polls CUPS for the status of all `active` jobs (PENDING, PROCESSING, HELD).
- Updates local DB state based on CUPS state.
- Handles timeouts for jobs that get stuck.
- Triggers SSE notifications on state changes.

### 4. Real-time Updates (SSE)
- Endpoint: `/api/jobs/stream`
- Clients (Dashboard & Status page) subscribe via `EventSource`.
- `JobSyncService` pushes updates to subscribers when DB records change.
- Includes keep-alive "comments" to prevent connection timeouts.

## Database Schema Overview

### User
- `sub`: OIDC Subject (Unique ID)
- `email`, `name`, `preferred_username`: Cached profile info

### PrintJob
- `id`: Short UUID (8 chars) for user-friendly URLs
- `cups_job_id`: Integer ID from CUPS
- `status`: Enum (pending, processing, completed, canceled, aborted, held, timed_out)
- `color_mode`: RGB/GRAY (detected from PPD options)
- `pages_printed`: Updated from CUPS completion data

## Development
- Run with `python run.py` (Debug mode, includes background sync)
- Keycloak runs in Docker: `docker-compose up -d`
- Database migrations: `flask db upgrade`
