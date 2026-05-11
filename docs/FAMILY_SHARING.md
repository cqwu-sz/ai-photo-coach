# Family Sharing for Pro (A2-2)

> Apple's Family Sharing lets up to 5 family members share an
> auto-renewable subscription. Enabling it is a one-click switch in
> App Store Connect *plus* one decision in our backend. Net effect on
> revenue: Apple's data shows ~10-30% lift in conversion when family
> shareable is on, especially for utility/photo apps. Worth it.

## Backend impact: minimal

When a Family Organizer subscribes, every family member receives an
**`appAccountToken`-less** `Transaction` from StoreKit. The
`originalTransactionId` is the organizer's; the `appAccountToken`
field tells us which member is using it (when set).

Today our `/iap/verify` already accepts any verified JWS and ties it
to the calling `user_id`. That means:

- Family member opens the app → does Sign in with Apple → calls
  `/iap/verify` with their own access token + the JWS Apple gave them
- We `upsert_subscription(user_id=<member>, original_transaction_id=<organizer's>)`
- `_evaluate_tier(member)` returns `pro`

The `UNIQUE(original_transaction_id)` constraint on `subscriptions`
**breaks this** — we'd overwrite the organizer's row when a member
verifies. Two ways to fix:

### Option A (recommended): drop the unique constraint

Allow N rows per `original_transaction_id`, one per `user_id`:

```sql
-- migration in user_repo._ensure_schema
DROP INDEX IF EXISTS sqlite_autoindex_subscriptions_1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sub_user_origtxn
    ON subscriptions(user_id, original_transaction_id);
```

Then change `upsert_subscription`'s `ON CONFLICT(original_transaction_id)`
to `ON CONFLICT(user_id, original_transaction_id)`.

### Option B: dedicated `family_links` table

Keep `subscriptions` 1:1 with Apple, add `family_links(user_id,
original_transaction_id, role)` and join in `_evaluate_tier`. More
code, more correct if you ever care about "who's the organizer".

### My pick

Option A unless you specifically need to differentiate organizer vs
member (e.g. "only the organizer can manage the subscription button").

## ASN webhook impact: handle DID_CHANGE_RENEWAL_STATUS_FAMILY_SHARED

When the organizer downgrades / cancels, Apple sends
`SUBSCRIBED` (subtype `RESUBSCRIBE`) or `EXPIRED` for *every* family
member's transaction. Our existing webhook handler already iterates
by `original_transaction_id`, so with Option A's `(user_id,
original_transaction_id)` uniqueness this works without changes.

Edge case: if a family member never opened the app, we have no
`subscription` row for them, so the webhook silently skips them.
That's fine — they get unlocked the moment they sign in.

## App Store Connect setup

1. App Store Connect → your app → In-App Purchases → `ai_photo_coach.pro.monthly`
2. **Family Sharing** → toggle ON
3. Save. Apple takes a few hours to propagate.

## iOS UI

In `AccountView` add a tiny note when the entitlement environment is
inherited:

```swift
if iap.entitlement.tier == "pro",
   let env = iap.entitlement.environment, env.contains("FamilyShared") {
    Text("由家庭共享提供").font(.caption).foregroundStyle(.secondary)
}
```

(StoreKit doesn't actually expose "FamilyShared" as a string today;
you need `Transaction.ownershipType == .familyShared`. Plumb that into
the JWS payload as a custom field if you want this UI.)

## Don't forget

- Family Sharing **cannot** be disabled once enabled. Apple is
  explicit: "you can't take it back". Make sure you're ready before
  flipping the switch.
- Sandbox testing: create a sandbox tester family in App Store
  Connect; both organizer and member need separate sandbox Apple IDs.
