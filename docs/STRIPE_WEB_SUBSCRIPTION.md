# Stripe Web Subscription Channel (A2-3)

> Optional. Adding a non-IAP subscription channel grows LTV (lower
> Apple cut, family/team plans, B2B pricing) but is a **landmine**
> against Apple's 3.1.3. Read this before writing a line of code.

## What Apple actually says (3.1.1 + 3.1.3)

Paraphrasing for the engineer audience:

- **3.1.1 — In-App Purchase**: features unlocked inside the iOS app
  for purchases made by the user must use IAP. Apple's cut applies.
- **3.1.3(b) — "Multiplatform Services"**: you MAY let users access
  content they bought elsewhere (Spotify, Netflix model). The iOS
  app must not "encourage or direct" users to alternative purchasing
  methods.
- **External Link Account Entitlement** (since 2022): if you're a
  reader/streaming/cloud-storage app, you can link to an external
  signup *once*, with a static modal Apple controls.

**Translation**: you CAN sell Pro on your website. Users who pay there
can sign in to your iOS app and the entitlement carries over (we
already support this — `subscriptions` table is keyed on `user_id`).
You CANNOT add a "Subscribe on web (cheaper!)" button inside the app.

## Architecture

```
┌──────────────┐  ① subscribe        ┌────────────────┐
│ Marketing    │────────────────────▶│  Stripe        │
│ site         │                     │  Checkout      │
└──────┬───────┘                     └────────┬───────┘
       │ ② signup with same email             │ ③ webhook
       ▼                                      ▼
┌──────────────┐                     ┌────────────────┐
│  /auth/siwa  │                     │ /stripe/webhook│
│  → user_id   │                     │ → upsert sub   │
└──────────────┘                     └────────────────┘
       │                                      │
       └──────────► users.tier='pro' ◀────────┘
```

Same DB table (`subscriptions`), new `environment='Stripe'` row. The
existing `_evaluate_tier` already picks the latest expiry winner so
adding a Stripe row is automatically respected.

## Implementation sketch

### 1. Stripe setup

```bash
pip install stripe  # not yet in requirements.txt
```

Env:
```
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
```

### 2. Backend route

```python
# backend/app/api/stripe_iap.py  (TO ADD)
import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from ..config import get_settings
from ..services import user_repo, auth as auth_svc

router = APIRouter(tags=["stripe"])

@router.post("/stripe/checkout")
async def create_checkout(user = Depends(auth_svc.current_user)):
    s = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": settings.stripe_pro_price_id, "quantity": 1}],
        success_url="https://your-domain/web/checkout/success",
        cancel_url="https://your-domain/web/checkout/cancel",
        client_reference_id=user.id,        # <-- KEY: ties Stripe sub to our user
        customer_email=...,                  # if you have it
    )
    return {"url": s.url}

@router.post("/stripe/webhook")
async def webhook(request: Request,
                   stripe_signature: str = Header()):
    raw = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            raw, stripe_signature, settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400)
    if event["type"] in ("customer.subscription.created",
                          "customer.subscription.updated",
                          "customer.subscription.deleted"):
        sub = event["data"]["object"]
        user_id = sub["metadata"].get("client_reference_id") or ...
        # Reuse user_repo.upsert_subscription with environment="Stripe"
    return {"ok": True}
```

### 3. UI surface

- **Web**: marketing landing page with "Get Pro" → Stripe Checkout.
- **iOS**: do NOT add a "Subscribe on web" button. Do add **"Already
  subscribed on the web? Sign in"** which routes through SIWA — the
  entitlement is already there.

### 4. App Store reviewer notes

When submitting a build that supports cross-platform Pro, write in
the App Review Notes field (App Store Connect → Version → App Review
Information):

> "Pro features are available via in-app subscription
> (`ai_photo_coach.pro.monthly`) and via web subscription on
> our marketing site. iOS users see only the in-app option;
> web subscribers can access their entitlement after Sign in
> with Apple."

This pre-empts the most common 3.1.3 confusion ("why does this user
have Pro without an IAP receipt?").

## Open questions before shipping

- [ ] Pricing parity: Apple's cut is 30% (15% after year 1, 15% for
  Small Business Program). Do you offer a discount on web that's
  large enough to matter but small enough to not provoke Apple?
- [ ] Refund policy: Stripe + Apple have different refund flows.
  Document for support.
- [ ] EU DSA / VAT: Stripe handles EU VAT collection automatically;
  confirm enabled.
- [ ] Family Sharing parity: Stripe doesn't have "family" out of the
  box. Either drop it on web or build seat-based plans.
