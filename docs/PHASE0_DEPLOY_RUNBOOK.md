# Phase 0 Deploy Runbook

> Step-by-step: take a freshly cloned repo from "tests pass on my
> laptop" → "live on App Store" without missing any compliance gate.
> Companion to `docs/MULTI_USER_AUTH.md` (which explains *why*); this
> doc is just *do this in order*.

Estimated wall time: **half a day** (excluding Apple's own review).

---

## 0. Prerequisites

- Apple Developer Program account (paid, individual or org)
- A domain you control (for privacy policy + ASN webhook)
- A Linux/macOS server with public HTTPS (the backend host)
- `python>=3.11`, `pip install -r backend/requirements.txt`

---

## 1. Generate secrets

```bash
APP_JWT_SECRET=$(openssl rand -hex 32)
REQUEST_TOKEN_SECRET=$(openssl rand -hex 32)
echo "APP_JWT_SECRET=$APP_JWT_SECRET"
echo "REQUEST_TOKEN_SECRET=$REQUEST_TOKEN_SECRET"
```

Store these in your secret manager (1Password / SOPS / AWS Secrets
Manager). **Never commit them to git.**

---

## 2. Apple Root CAs

Download two PEM files Apple publishes:

```bash
mkdir -p backend/app/data

# For IAP / ASN V2 JWS chain validation:
curl -fSL https://www.apple.com/certificateauthority/AppleRootCA-G3.cer \
  | openssl x509 -inform DER -out backend/app/data/apple_root_ca_g3.pem

# For App Attest:
curl -fSL https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem \
  -o backend/app/data/apple_app_attest_root_ca.pem
```

Verify both files start with `-----BEGIN CERTIFICATE-----`.

> Without these files the corresponding services run in
> *unverified-decode mode* (logged as WARN). The app still works but
> JWS forgery becomes possible. **Do not ship to App Store without
> them.**

---

## 3. App Store Connect — IAP product

1. App Store Connect → Apps → your app → **In-App Purchases** → **+**
2. Type: **Auto-Renewable Subscription**
3. Reference name: `AI Photo Coach Pro - Monthly`
4. **Product ID: `ai_photo_coach.pro.monthly`** (must match
   `IAPManager.productId` in code)
5. Subscription group: `Pro` (create new)
6. Pricing: ¥18 / month (or your preferred — keep it consistent across
   regions or define per-region prices)
7. Submit for review **alongside** the app build, not separately.

Once Apple approves the product, set `IAPManager.useShadowPro = false`
in code (already the default). For development you can leave it true
to bypass real purchases.

---

## 4. App Store Connect — Server Notifications V2

1. App Store Connect → Apps → your app → **App Information** →
   **App Store Server Notifications**
2. **Production URL**: `https://<your-prod-domain>/apple/asn`
3. **Sandbox URL**: `https://<your-staging-domain>/apple/asn`
4. **Version**: V2 (check the V2 toggle)
5. Save.

Test:

```bash
# After deploy:
curl https://<your-prod-domain>/healthz
# → {"status":"ok",...}

# Apple Connect has a "Request a Test Notification" button — use it.
# Verify in your logs:
#   apple iap chain verify failed, falling back: ...   (if root CA missing)
#   apple iap fingerprint ok, type=TEST                (when configured)
```

---

## 5. App Store Connect — App Store Server API key

Optional but **strongly recommended** for the safety-net cron
(`scripts/reconcile_subscriptions.py`):

1. App Store Connect → Users and Access → **Keys** (top tab) →
   **In-App Purchase**
2. Click **+**, name it `iap-reconcile`, access: `Admin` is overkill;
   `App Manager` is enough.
3. Download the `.p8` (Apple shows it once).
4. Note the **Issuer ID** (top of Keys page) and **Key ID**.

Set on your server:

```bash
APPLE_IAP_BUNDLE_ID=com.your.bundleid
APPLE_IAP_ISSUER_ID=...
APPLE_IAP_KEY_ID=...
APPLE_IAP_PRIVATE_KEY_PATH=/etc/secrets/AuthKey_XXX.p8
```

Add cron:

```cron
0 * * * *  cd /opt/ai-photo-coach/backend && /usr/bin/python -m scripts.reconcile_subscriptions
```

When env vars are missing the script logs a notice and exits 0 — safe
to schedule even before you've configured it.

---

## 6. Sign in with Apple

1. Apple Developer → **Certificates, Identifiers & Profiles** →
   Identifiers → your App ID → check **Sign In with Apple**.
2. Xcode → target → **Signing & Capabilities** → **+ Capability** →
   **Sign in with Apple**.
3. Xcode → **+ Capability** → **App Attest**.
4. In your iOS app's `Info.plist`:

```xml
<key>com.apple.developer.devicecheck.appattest-environment</key>
<string>production</string>   <!-- or "development" for dev -->
```

5. Backend env:

```bash
APPLE_SIWA_BUNDLE_ID=com.your.bundleid     # MUST match the Xcode target
APPLE_SIWA_TEAM_ID=ABCD1234EF              # 10-char Team ID from Developer portal
```

---

## 7. Privacy Manifest

Already at `ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy`. In Xcode:

1. Drag the file into the project navigator.
2. Build phases → **Copy Bundle Resources** → confirm it's listed.
3. **Product → Archive → Distribute → Generate App Privacy Report**.
   The report should list "Device ID, Coarse location, Photos, Email"
   under "Data linked to user".

If you add 3rd-party SDKs later, each one ships its own
`.xcprivacy` — Xcode merges them into the generated report.

---

## 8. Privacy policy + EULA URLs

Default: app uses the bundled `web/privacy.html` (served from your
backend at `/web/privacy.html`). It's **a template — replace
contact email + entity name before submission**.

For a real marketing-domain URL, set on the backend:

```bash
PRIVACY_POLICY_URL=https://your-domain.com/privacy
EULA_URL=https://your-domain.com/eula     # optional; defaults to Apple stdeula
```

The iOS app reads both from `/healthz` so you can rotate them without
shipping a new release.

---

## 9. Backend env — final checklist

```bash
APP_ENV=production
ENFORCE_REQUIRED_SECRETS=true             # refuse to start when misconfigured

APP_JWT_SECRET=<from step 1>
REQUEST_TOKEN_SECRET=<from step 1>
CORS_ALLOW_ORIGINS=https://your-domain.com   # comma-sep, NO localhost in prod

APPLE_SIWA_BUNDLE_ID=<from step 6>
APPLE_SIWA_TEAM_ID=<from step 6>
APPLE_IAP_BUNDLE_ID=<from step 6>
APPLE_IAP_ENVIRONMENT=Production           # 'Sandbox' for staging
# (optional) App Store Server API:
APPLE_IAP_ISSUER_ID=
APPLE_IAP_KEY_ID=
APPLE_IAP_PRIVATE_KEY_PATH=

# After iOS v1.1 (with AuthManager) is rolled out to >95% of users:
ENABLE_LEGACY_DEVICE_ID_AUTH=false

# Optional but recommended:
REDIS_URL=redis://your-redis:6379/0       # cross-worker rate limit
RATE_LIMIT_PRO_MULTIPLIER=5               # Pro users get 5× headroom
ANONYMOUS_ACCOUNT_TTL_DAYS=30
ENABLE_DDTRACE=true                        # if Datadog APM is set up
```

Boot:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

On first boot you should see (and grep for):

```
INFO startup_check: APP_JWT_SECRET — ok
INFO startup_check: REQUEST_TOKEN_SECRET — ok
INFO startup_check: CORS_ALLOW_ORIGINS — https://your-domain.com
INFO startup_check: apple_root_ca_g3.pem — backend/app/data/apple_root_ca_g3.pem
INFO startup_check: apple_app_attest_root_ca.pem — backend/app/data/apple_app_attest_root_ca.pem
INFO Starting AI Photo Coach backend
```

If you see `RuntimeError: refusing to start: ... required setting(s)
missing` — fix env, redeploy. The startup check is your friend.

---

## 10. Post-deploy smoke

```bash
# Health
curl https://your-domain.com/healthz

# Auth
curl -X POST https://your-domain.com/auth/anonymous \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"runbook-test"}'
# → 200 with access_token + refresh_token

# Use the token to hit /me
TOKEN=<paste access_token>
curl https://your-domain.com/me \
  -H "Authorization: Bearer $TOKEN"
# → {"user_id":"...","is_anonymous":true,"tier":"free",...}

# Verify CORS preflight
curl -i -X OPTIONS https://your-domain.com/analyze \
  -H "Origin: https://your-domain.com" \
  -H "Access-Control-Request-Method: POST"
# → access-control-allow-origin: https://your-domain.com  (NOT *)

# Trigger ASN test from App Store Connect, then:
grep apple_root_ca /var/log/ai-photo-coach.log     # should NOT log "missing"
grep "asn webhook"                                # should show test notification arriving
```

---

## 11. iOS submission checklist

- [ ] `IAPManager.useShadowPro = false`
- [ ] App ID has Sign in with Apple + App Attest enabled
- [ ] `PrivacyInfo.xcprivacy` in Copy Bundle Resources
- [ ] All `Info.plist` usage strings present (see
  `ios/AIPhotoCoach/App/InfoPlistEntries.md`)
- [ ] In `Info.plist`: `com.apple.developer.devicecheck.appattest-environment = production`
- [ ] Sandbox tester account created in App Store Connect →
  Users and Access → Sandbox → Testers; verify subscribe / cancel /
  refund flow on a real device
- [ ] Privacy policy URL filled in App Store Connect
- [ ] Subscription metadata (description, marketing copy, screenshots)
  filled — Apple rejects 3.1.2 if missing

---

## 12. Day-2 ops

| Frequency | Action |
|---|---|
| Hourly | `scripts/reconcile_subscriptions` (cron) |
| Daily | `scripts/daily_seed_active_areas` (existing) |
| Daily | Anonymous-account TTL sweep (built into the lifespan loop) |
| Weekly | `weekly_poi_boost`, `weekly_style_cluster` (existing) |
| Per release | Smoke this checklist's §10 against staging first |

When something feels off:

```bash
# What tier is the user on right now?
curl https://your-domain.com/me/entitlements -H "Authorization: Bearer $TOKEN"

# How many auth requests / hour, by method?
curl https://your-domain.com/metrics | grep auth_total

# Has Apple been hitting our webhook lately?
curl https://your-domain.com/metrics | grep asn_total

# Any rate-limit pressure?
curl https://your-domain.com/metrics | grep rate_limit_total
```
