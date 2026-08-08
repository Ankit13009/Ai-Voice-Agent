# Getting your first call working

Everything here is free except the Indian phone number, which comes last.

`backend/.env` already has `JWT_SECRET`, `ENCRYPTION_KEY`, `VAPI_WEBHOOK_SECRET`
and `WHATSAPP_VERIFY_TOKEN` generated for you. The three sections below are the
credentials you have to fetch yourself.

Do steps 1, 2 and 3 in parallel: WhatsApp approval and Exotel KYC both take
days, so start them before you need them.

---

## Step 1 — VAPI (30 minutes, free)

Gets you a working agent you can talk to in the browser.

1. Sign up at <https://dashboard.vapi.ai>. You get **$10 credit and 60 free
   minutes, no card required**.
2. Go to **Account → API Keys**. Copy both keys into `backend/.env`:
   ```
   VAPI_API_KEY=      # private. Server-side only, never sent to a browser.
   VAPI_PUBLIC_KEY=   # public. Safe in the browser; powers the Test call button.
   ```
3. *(Optional but recommended)* In **Provider Keys**, paste the Deepgram and
   Anthropic keys from `~/Projects/voice-receptionist/backend/.env`. Those calls
   then bill to your own accounts instead of eating the 60 free minutes.

**Expose your server**, because VAPI has to reach your tool webhook:

```bash
ngrok http 8800
# copy the https URL into backend/.env:
#   PUBLIC_BASE_URL=https://<subdomain>.ngrok-free.app
```

Restart the backend, then sign in as a **business owner** (not the superadmin)
and hit **Save** on any Settings card. That provisions the VAPI assistant for
that business from the master template, and the Test call button goes live.

> Do not build the assistant by hand in VAPI's dashboard. The platform creates
> one per client, with that client's prompt, tools, webhook URL and secret
> already wired. A hand-made assistant has none of those and cannot book.

> The tool webhook only works while ngrok is running and `PUBLIC_BASE_URL`
> matches the current ngrok URL. Free ngrok URLs change on every restart.

### What to check on that first call

- Does it understand you when you mix Hindi and English?
- Does it offer only real slots, and refuse to invent one?
- Does it stay inside its rules (a clinic agent must not comment on symptoms)?
- How long is the pause before it replies?

---

## Step 2 — Google Calendar (20 minutes, free)

Without this the agent cannot check availability or book anything.

1. <https://console.cloud.google.com> → create a project.
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **Credentials → Create credentials → OAuth client ID → Web application**.
4. Add the authorised redirect URI, exactly:
   ```
   http://localhost:8800/api/v1/integrations/google/callback
   ```
   In production this becomes your real domain. It must match character for
   character or Google rejects the login.
5. Copy the client ID and secret into `backend/.env`.
6. In the dashboard: **Settings → Integrations → Connect**, and sign in with the
   Google account that owns the calendar.

Test it by creating an event in that Google Calendar by hand, then asking the
agent for slots at that time. It should not offer them. That single check proves
the two-way sync works.

---

## Step 3 — WhatsApp (start now, ~2 days for approval)

Free, and no Facebook Business verification needed for testing.

1. <https://developers.facebook.com> → **Create App** → **Business** type. When
   prompted, create a **Test Business Account**: that is enough for the sandbox.
2. Add the **WhatsApp** product. You get a free test number.
3. Under **API Setup**, add up to **5 recipient numbers** (yours, Nishant's).
   Messages to those are unlimited and free.
4. Copy into `backend/.env`:
   - `WHATSAPP_ACCESS_TOKEN` (use a **System User** token, not the 24-hour one)
   - `WHATSAPP_PHONE_NUMBER_ID`
   - `WHATSAPP_BUSINESS_ACCOUNT_ID`
   - `WHATSAPP_APP_SECRET` from **App Settings → Basic**
5. Subscribe the webhook to `https://<ngrok>/webhooks/whatsapp`, using the
   `WHATSAPP_VERIFY_TOKEN` already in your `.env`.
6. **Submit the templates.** Open `backend/app/services/whatsapp.py`, take each
   entry in `TEMPLATES`, and submit it in WhatsApp Manager under category
   **Utility**, in both English and Hindi.

Approval is the long pole. Nothing sends until it clears, so submit on day one.

---

## Step 4 — Exotel (only once steps 1-3 work)

This is the paid part, and the only way to get a real Indian number.

You cannot buy an Indian number from VAPI: TRAI requires SIP termination on an
Indian server. Exotel holds a Unified License as a VNO, which makes it the
compliant bridge. Exotel publishes an official connector:
<https://github.com/exotel/Exotel-Vapi-Connector>

1. Create an Exotel account and start **KYC**. It takes days and is
   **region-specific**: you can only buy numbers in cities where KYC is done.
2. Fund the account (≈₹500 minimum to buy an ExoPhone) and buy one number.
3. Email `hello@exotel.com` to enable **vSIP trunking**, which is not on by default.
4. Create the SIP trunk, point an FQDN destination at your VAPI bot
   (`your-bot.sip.vapi.ai:5060`), map the ExoPhone to the trunk, and whitelist
   VAPI's IPs `44.229.228.186` and `44.238.177.138`.
5. On the VAPI side create a BYO SIP credential (gateway port `5070`) and link
   the number to the assistant.
6. Put that number on the business in the dashboard.

**Measure latency on the very first real call.** VAPI runs in AWS Oregon, so
Indian audio crosses the Pacific and back. If the pause feels too long, the fix
is architectural (an India-hosted pipeline such as Sarvam), and you want to know
that before you have sold to ten clinics.

### Client-side setup

Clients keep their advertised number and forward to the ExoPhone. Dialled once
from the business phone:

```
Forward when no answer   *61*<ExoPhone>#
Forward when busy        *67*<ExoPhone>#
Forward everything       *21*<ExoPhone>#
```

Codes vary by carrier. "Forward on no answer" is the easier first sale: the AI
catches only what they already miss.

---

## Quick reference

| What | Cost | Lead time | Blocks |
|---|---|---|---|
| VAPI | Free ($10 + 60 min) | Minutes | Everything |
| Google Calendar | Free | Minutes | Booking |
| WhatsApp | Free (5 test numbers) | **~2 days** | Confirmations, reminders |
| Exotel | ₹500+ | **Days (KYC)** | Real phone calls |
