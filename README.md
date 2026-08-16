# AI Receptionist Platform

A multi-tenant AI phone receptionist you onboard any appointment-based business
onto. A customer calls, the agent answers in Hindi or English, checks that
business's real Google Calendar, books, reschedules, or cancels, and WhatsApp
confirmations and reminders follow automatically.

Clinics, dental practices, salons, gyms, law firms, home services, and vets ship
as presets. Adding another trade is one entry in `app/agent/presets.py`, or just
the `general` preset with the fields edited. **No code path is trade-specific.**

```
Patient call  ──▶  VAPI assistant (Hindi + English)
                     │
                     ├── tool webhook ──▶  FastAPI  ──▶  Google Calendar (freeBusy / events)
                     │                        │
                     │                        └────────▶  WhatsApp Cloud API (confirm + reminders)
                     │
                     └── end-of-call ────▶  transcript, outcome, cost  ──▶  Next.js dashboard
```

Frontend and backend are separate applications with separate deploys. They
share nothing but the HTTP contract, which is typed on both sides.

| | Stack | Port |
|---|---|---|
| `backend/` | FastAPI, SQLAlchemy async, Postgres | 8000 |
| `frontend/` | Next.js 14 App Router, TypeScript, Tailwind | 3000 |

---

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in what you have; SQLite is used if DATABASE_URL is empty

python seed.py --email you@yourdomain.in --password 'a-strong-password' --demo
uvicorn app.main:app --reload --port 8000
```

`--demo` creates **two different trades** (a clinic and a salon) on the same
code, each with sample calls and appointments. Signing into each shows the
platform relabelling itself: Patients/Doctors versus Clients/Stylists.

Interactive API docs: <http://localhost:8000/docs> (disabled in production).

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at the backend
npm run dev
```

Sign in at <http://localhost:3000> with the demo owner
(`owner@sunriseclinic.in` / `demo-password-123`) or the superadmin you seeded.

---

## The API contract

Every endpoint under `/api/v1` returns the same envelope. There are no
exceptions, including validation failures, 404s, and unhandled crashes, because
the response shape is enforced by global exception handlers rather than by
convention.

**Success**

```json
{
  "success": true,
  "data": { "id": "…", "name": "…" },
  "meta": null,
  "message": "Appointment booked and added to the calendar.",
  "request_id": "req_9f2c4a1b8e3d5f60",
  "timestamp": "2026-08-08T10:00:00Z"
}
```

**Paginated success** — identical, with `data` as an array and `meta` populated:

```json
{
  "success": true,
  "data": [ … ],
  "meta": {
    "page": 1, "page_size": 20, "total": 57,
    "total_pages": 3, "has_next": true, "has_prev": false
  },
  "message": null,
  "request_id": "req_…",
  "timestamp": "…"
}
```

**Error**

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Some of the submitted fields are invalid.",
    "details": [
      { "field": "customer_phone", "message": "String should match pattern '^\\+[1-9]\\d{7,14}$'" }
    ]
  },
  "request_id": "req_…",
  "timestamp": "…"
}
```

`success` is the discriminator. On the frontend, `ApiResponse<T>` is a
discriminated union, so TypeScript will not let you read `data` without first
narrowing on success.

### Error codes

Stable and machine-readable. Defined in `backend/app/core/errors.py` and
mirrored as a union in `frontend/types/api.ts`.

| Code | Status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Field-level problems; see `details`. |
| `BAD_REQUEST` | 400 | Malformed request that is not field-specific. |
| `UNAUTHENTICATED` | 401 | Missing or unusable token. |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password. |
| `TOKEN_EXPIRED` | 401 | Access token expired; the client refreshes and retries. |
| `TOKEN_INVALID` | 401 | Token is malformed, wrong type, or revoked. |
| `FORBIDDEN` | 403 | Authenticated but not allowed. |
| `INSUFFICIENT_ROLE` | 403 | Role does not permit this action. |
| `BUSINESS_ACCESS_DENIED` | 403 | Attempted to act on another business. |
| `WEBHOOK_SIGNATURE_INVALID` | 403 | Webhook signature or secret did not verify. |
| `NOT_FOUND` | 404 | No such resource *in your business*. |
| `CONFLICT` | 409 | State conflict, e.g. a past appointment time. |
| `ALREADY_EXISTS` | 409 | Unique constraint, e.g. duplicate business slug. |
| `SLOT_UNAVAILABLE` | 409 | The time was taken between offer and booking. |
| `RATE_LIMITED` | 429 | Too many attempts; `Retry-After` is set. |
| `INTEGRATION_NOT_CONFIGURED` | 503 | Google Calendar / WhatsApp / VAPI not set up. |
| `UPSTREAM_ERROR` | 502 | A third-party API failed. |
| `INTERNAL_ERROR` | 500 | Unhandled fault. Quote the `request_id`. |

`request_id` appears on every response and in every server log line, so a user
reporting an error can be traced to the exact request.

### Webhooks are not part of this contract

`/webhooks/vapi/*` and `/webhooks/whatsapp` answer to VAPI's and Meta's fixed
external formats and authenticate by signature rather than JWT. They are
deliberately outside `/api/v1`.

---

## Security

Each item below is enforced in code, not documented as an intention.

**Authentication.** Bcrypt (cost 12) password hashing. JWT access tokens (30
min) and refresh tokens (14 days) whose SHA-256 hashes are stored server-side so
sessions can actually be revoked. Refresh tokens are single-use and rotated;
presenting one twice is treated as theft and revokes every session for that
user. The user row is re-read on every request, so deactivating an account takes
effect immediately rather than whenever the token happens to expire.

**Tenant isolation.** The active business id comes from the verified JWT, never
from a request parameter. Passing `?business_id=<someone else's>` is ignored for
business users and rejected outright. Single-object reads go through
`scoped_get()`, which filters by primary key *and* business id in one query, so
"exists but not yours" and "does not exist" are indistinguishable, in both the
response and its timing.

Verified against a second tenant:

```
Verified with a clinic owner attacking a salon tenant:

GET   /appointments/{salon's id}              → 404 NOT_FOUND
GET   /customers/{salon's id}                 → 404 NOT_FOUND
GET   /calls?business_id={salon}              → 403 BUSINESS_ACCESS_DENIED
PATCH /appointments/{salon's}/cancel          → 404 NOT_FOUND
PATCH /businesses/me?business_id={salon}      → 403 BUSINESS_ACCESS_DENIED
GET   /onboarding/business-types (as owner)   → 403 INSUFFICIENT_ROLE
```

**Webhook authentication.** VAPI webhooks require a shared secret compared with
`hmac.compare_digest`. WhatsApp webhooks verify Meta's `X-Hub-Signature-256`
HMAC over the **raw** request body. Both refuse to run at all if the secret is
unset, rather than failing open.

**Secrets at rest.** Google OAuth refresh tokens are Fernet-encrypted. No
endpoint ever returns them; the settings response exposes only whether the
connection works and which account it points at.

**CORS.** An explicit origin allowlist. No wildcards and no regex like
`.*\.vercel\.app`, which would let any attacker-controlled preview deployment
read authenticated responses.

**Rate limiting.** Sliding window on login, refresh, password change, and
onboarding.

**Other.** Login is constant-time across "no such user" and "wrong password", so
it cannot be used to enumerate accounts. Pagination is capped at 100 per page.
Security headers and a strict CSP on every response. Privileged actions are
written to an append-only audit log with actor, IP, and request id. API docs are
disabled in production. Startup refuses to boot a production deploy that is
missing any security-critical setting.

---

## Onboarding any business type

This is the part that makes it a platform rather than one vertical.

**One codebase, config-driven.** A tenant row carries its own vocabulary,
persona, intake questions, rules, and escalation path. The agent's prompt, the
tool descriptions, and the dashboard's labels are all composed from those
columns at runtime. Nothing in `app/` branches on trade.

**Presets are a starting point, not a constraint.** `GET /api/v1/onboarding/business-types`
returns the presets; picking one pre-fills the form. Every value it supplies is
then an editable column on the tenant, so a business nobody anticipated is a
form submission, never a deploy.

| Preset | Customers / Staff | Rules | Escalation |
|---|---|---|---|
| `clinic` | Patients / Doctors | no diagnosis, no prices, no symptom detail | medical emergency |
| `dental` | Patients / Dentists | no treatment advice, no price quotes | swelling, bleeding |
| `salon` | Clients / Stylists | ask the service, no invented prices | none |
| `gym` | Members / Trainers | no training or nutrition advice | none |
| `law` | Clients / Solicitors | no legal advice, no fee quotes, no detail by phone | deadline within 2 days |
| `home_services` | Customers / Technicians | address required, judge urgency, no price quotes | gas, fire, flooding |
| `veterinary` | Pet owners / Vets | no diagnosis, no dosage advice | animal in distress |
| `general` | Customers / Team members | minimal | none |

The same call, in two trades, from identical code:

```
Sunrise Multispeciality Clinic (business_type=clinic)
  "You are Asha, the phone receptionist for Sunrise Multispeciality Clinic,
   a medical clinic. …with a patient or someone calling on their behalf."
  Rules:  not a clinician, never diagnose, no symptom detail, no prices
  Urgent: chest pain / bleeding → tell them to go to emergency, end the call
  Intake: … 4. Preferred doctor (only if they offer it)

Glow Studio (business_type=salon)
  "You are Riya, the phone receptionist for Glow Studio, a salon and spa.
   …with a client or someone calling on their behalf."
  Rules:  ask which service, never quote a price, offer the stylist's next opening
  Urgent: (none)
  Intake: … 4. Preferred stylist (only if they offer it)
```

### Credentials: set once, works from then on

| Credential | Scope | Who sets it |
|---|---|---|
| VAPI API key, Google client id/secret, WhatsApp token | **Platform** (`.env`) | You, once |
| VAPI assistant, WhatsApp number, phone number | **Per tenant** | Created automatically on onboard |
| Google Calendar OAuth | **Per tenant** | The business owner connects their own |

So onboarding business #100 is one authenticated POST. No code, no redeploy.

---

## Frontend architecture

**Design system first.** `components/ui/` is the only place a button, badge,
table, dialog, or form control is defined. Pages import from
`@/components/ui` and compose; they do not style raw elements. Colour, radius,
shadow, and type scale live as CSS custom properties in `styles/globals.css`
and are surfaced to Tailwind as semantic names (`bg-surface`, `text-ink`,
`border-line`), so components cannot reach a raw hex value and a rebrand is one
file. Dark mode redefines the same variables rather than adding a `dark:`
variant to every element.

Domain status to visual tone is mapped once in `Badge.tsx`, which is what makes
"cancelled" the same red on the appointments table, the call log, and the
patient detail page.

**Typed API interfaces.** `types/api.ts` mirrors the backend exactly: the
envelope, every error code, every entity, and every query shape. Nothing in the
app types a response inline.

**One HTTP client.** `lib/api/client.ts` unwraps the envelope in a single place,
so no component touches `response.success`. Failures arrive as `ApiError`
carrying the backend's code, message, field details, and request id. Expired
access tokens are refreshed transparently, and concurrent requests share one
in-flight refresh, since refresh tokens are single-use and parallel refreshes
would log the user out.

`lib/api/endpoints.ts` types every route, so a renamed endpoint is a compile
error rather than a runtime 404.

---

## Reusing the old voice-receptionist repo

This project carries over the working parts of `NishantDixit1/voice-receptionist`
and replaces the rest:

| Carried over | Replaced |
|---|---|
| Multi-tenant routing by dialed number | Pipecat self-hosted pipeline → VAPI |
| Prompt architecture (config-injected persona, live time) | Cal.com → Google Calendar direct |
| Tool-calling shape for booking | Twilio owner SMS → WhatsApp Cloud API |
| Dashboard concept and page structure | Home-services domain → clinic domain |
| One-command onboarding | Unauthenticated, un-scoped `/api` → auth + tenant scoping |

---

## Setting up the integrations

### Google Calendar

1. Google Cloud Console → enable the **Google Calendar API**.
2. Credentials → **OAuth client ID** → Web application.
3. Authorised redirect URI:
   `https://<your-api>/api/v1/integrations/google/callback`
4. Put the client id, secret, and redirect URI in `.env`.
5. In the dashboard: Settings → Integrations → **Connect**.

Two-way sync comes free from this: `freebusy.query` means an event a
receptionist types straight into Google immediately blocks the phone agent, and
`events.insert/patch/delete` means the agent's bookings appear in the clinic's
real calendar.

### WhatsApp (Meta Cloud API, not WATI)

1. Meta Business Manager → WhatsApp → API Setup. Use a **System User** token,
   not the 24-hour temporary one.
2. Submit the templates in `backend/app/services/whatsapp.py` (`TEMPLATES`)
   under category **Utility**, in both English and Hindi. Approval takes a few
   hours to two days.
3. Subscribe the webhook to `https://<your-api>/webhooks/whatsapp` with your
   `WHATSAPP_VERIFY_TOKEN`.

Cost: no subscription. Utility templates are roughly ₹0.115 each in India and
free inside a 24-hour window opened by a patient replying, so a clinic running
500 appointments a month pays around ₹175 against a WATI plan starting near
₹2,500. The tradeoff is that template wording is fixed once approved; only the
variables change.

### VAPI

Set `VAPI_API_KEY` and `VAPI_WEBHOOK_SECRET`. Onboarding a clinic creates its
assistant automatically from the master template in
`backend/app/services/vapi.py`. Set `VAPI_PHONE_NUMBER_ID` to also attach a
number during onboarding.

The assistant uses Deepgram `nova-2` with `language: multi` for Hindi/English
code-switching, an Azure `hi-IN` voice that handles both languages without
switching voice mid-call, and Claude Haiku for low phone latency.

---

## Adding a business

One authenticated request as a superadmin creates the tenant, its owner login,
its staff, its services, and its VAPI assistant, seeded from the chosen preset:

```bash
curl -X POST https://<your-api>/api/v1/onboarding/businesses \
  -H "Authorization: Bearer <superadmin token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Glow Studio",
    "slug": "glow-studio",
    "phone_number": "+911140005678",
    "business_type": "salon",
    "owner_email": "owner@glowstudio.in",
    "owner_password": "a-strong-password",
    "timezone": "Asia/Kolkata",
    "primary_language": "hi-en",
    "staff_members": [{ "name": "Kabir Shah", "specialization": "Colour Specialist" }]
  }'
```

Override any preset value inline (`labels`, `agent_rules`, `intake_fields`,
`escalation_instructions`), or leave them out and edit later in Settings.
Anything onboarding could not finish automatically comes back in `next_steps` as
a checklist, rather than failing the whole business because one third party was
down.
