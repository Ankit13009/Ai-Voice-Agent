# Deploying (free tier)

Three services, no card required:

| Part | Where | Cost |
|---|---|---|
| Postgres | Neon | free |
| Backend (FastAPI) | Render | free |
| Frontend (Next.js) | Vercel | free |

**The one catch, and how it is handled:** a free Render service sleeps after 15
minutes idle and takes 30-60 seconds to wake. That is fatal for a live call,
because VAPI's tool webhook would time out mid-conversation and the caller would
hear the agent fail to find any slots. Step 5 sets up a keep-alive ping, which
prevents it entirely. Do not skip it.

---

## 1. Push the repo to GitHub

Render deploys from a repo.

```bash
cd ~/Projects/clinic-receptionist
gh repo create clinic-receptionist --private --source=. --push
```

## 2. Postgres on Neon (5 min, free)

1. <https://neon.tech> → sign up with GitHub → **Create project**
2. Region: **AWS US West (Oregon)** — same region as the backend, so queries
   during a live call do not cross the country
3. Copy the **connection string**

It will look like
`postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require&channel_binding=require`.
Paste it as-is; the app strips the parameters asyncpg cannot accept.

## 3. Backend on Render (10 min, free)

1. <https://render.com> → sign up with GitHub
2. **New → Web Service** → pick the repo
3. Render reads `render.yaml`, so runtime, region, root directory and health
   check are already set. Confirm:
   - Runtime **Docker**, Root directory **backend**, Region **Oregon**, Plan **Free**
4. Add the environment variables. Run `python generate_prod_env.py` locally to
   produce them, then paste each into **Environment** in the dashboard.
5. Deploy, and wait for the health check to pass.

Your API is then at `https://clinic-receptionist-api.onrender.com`.

## 4. Frontend on Vercel (5 min, free)

```bash
cd frontend
vercel --prod
```

Set one environment variable in the Vercel dashboard:

```
NEXT_PUBLIC_API_BASE_URL = https://clinic-receptionist-api.onrender.com
```

Then redeploy so it takes effect.

## 5. Keep-alive (required on the free tier)

<https://cron-job.org> → free account → create a job:

- URL: `https://clinic-receptionist-api.onrender.com/health`
- Every **10 minutes**

Without this the backend sleeps and the first call after a quiet period fails.

## 6. Point everything at the new URLs

Four places still reference localhost or ngrok:

**Render environment**
```
PUBLIC_BASE_URL           https://clinic-receptionist-api.onrender.com
CORS_ORIGINS              https://<your-vercel-domain>
GOOGLE_OAUTH_REDIRECT_URI https://clinic-receptionist-api.onrender.com/api/v1/integrations/google/callback
```

**Google Cloud Console** → Credentials → your OAuth client → add the same
redirect URI. It must match character for character.

**Meta** → WhatsApp → Configuration → webhook callback URL:
`https://clinic-receptionist-api.onrender.com/webhooks/whatsapp`

**VAPI** — nothing to do by hand. The assistants read `PUBLIC_BASE_URL`, so
saving Settings once in the dashboard re-pushes both with the new webhook URLs.

## 7. Seed the first superadmin

In Render → **Shell**:

```bash
python seed.py --email you@yourdomain.in --password 'a-strong-password'
```

Omit `--demo` in production unless you want the sample clinic and salon.

---

## Verify it works

```bash
curl https://clinic-receptionist-api.onrender.com/health
```

Then sign in on the Vercel URL, open Settings, and check:

- Google Calendar still shows connected (reconnect if the redirect URI changed)
- Save Settings once, so the VAPI assistants pick up the new webhook URL
- Run a browser test call and confirm booking still writes to Google Calendar

## When to leave the free tier

The free stack is fine for demos and a first client trial. Move to a paid plan
(Fly or Render Starter, roughly $5-7/month) when any of these becomes true:

- You have a paying client, and a cold start is now their missed call
- The keep-alive is not enough, e.g. Render's free hours run out mid-month
- You need more than one instance, at which point the in-process rate limiter
  and reminder scheduler both need revisiting (see the README's limitations)
