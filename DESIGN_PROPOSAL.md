# Account screen, and three free valuations

A proposal. Nothing in here is built. `app/mockup/` renders the screens
against the real theme tokens and is throwaway; delete it once this is
settled.

---

## 1. Why the account screen looks broken

One line:

```dart
// account_screen.dart:126
backgroundColor: colors.accentSurface.withValues(alpha: 0.0),
```

A fully transparent Scaffold background. Nothing paints behind it, so the
black below shows through, and the cards - which are `accentSurface`,
`#E9EEFD`, a pale blue - sit on it as light slabs. That is the "light
cards on a pure-black background" exactly. The theme is fine; this screen
opted out of it.

Three further reasons it does not match the app:

| Now | Rest of the app |
| --- | --- |
| Cards are `accentSurface` at `AppRadius.control` (14) | `cardTheme`: `surface`, hairline border, radius 20 |
| Actions are `OutlinedButton.icon`, stacked | Rows inside a card, hairline-divided |
| Danger zone is a tinted box among the others | Nothing separates it from ordinary actions |

### What changes

- **Delete line 126.** The Scaffold takes the theme's
  `scaffoldBackgroundColor` - `#F5F7FA` light, `#0B0E13` dark - and the
  cards become `surface`, so the contrast is card-against-page rather
  than light-against-black.
- **Profile card**: avatar initial on `accentSurface`, email at
  `titleMedium`, "Signed in with Google" beneath at `bodySmall`.
- **Plan row**: one line, "Unlimited valuations", with a `FREE PLAN`
  pill. This is where §3 becomes visible to a signed-in reader.
- **What we hold**: unchanged in substance - it is the point of the
  screen - but as a proper card with hairline rows and the "no password,
  no payment details" note on `surfaceContainerHighest` as a footer.
  One row added: **Valuations this month**, because after §3 we hold
  that too and this list is where we say so.
- **Your data**: Download / Sign out become tappable rows with a hint
  line each, in one card.
- **Danger zone**: a labelled rule - `DANGER ZONE` between two hairlines
  - then the delete card in `negativeSurface` with a `negative` border.
  Separated, not merely coloured.

Spacing uses `AppGap.section` between sections and `AppGap.block` within,
which is the scale the rest of the app already uses.

---

## 2. Signed out

Today: one centred grey paragraph. No way to sign in from the screen whose
name is Account.

Proposed: a left-aligned page, not a centred apology.

- An icon tile, `Keep your work` at `headlineMedium`.
- **One line**: "An account adds a watchlist and saved valuations, kept
  against your Google sign-in so they are still there on your next
  device."
- **Sign in with Google** - Google's mark, their wording, on `surface`
  with a hairline. 52px tall.
- Under it, quietly: "Valuing companies works without an account."
- A three-row card of what it gives: watchlist, reopenable valuations,
  unlimited valuations.
- A closing note that we ask Google only for an email and an identifier,
  and that no password reaches us.

**The mockup draws the Google G with a CustomPainter so it needs no new
asset. Shipping it means bundling Google's official mark** - their
branding guidelines require the supplied asset, and an approximation on a
sign-in button is a compliance problem, not a detail.

---

## 3. Three free valuations, then sign-in

### What already exists

`entitlement.py` and `identity.py` were written for this and are not yet
wired to anything (`grep` finds no use of either in `api.py`). The shape
is right: an `Entitlement` record rather than a boolean, a counter keyed
on a verified `uid`, and a documented refusal to let the client decide.

Two things in them need changing, both small.

### 3.1 Limits become configuration

`valuations_per_week` is the wrong axis. Anonymous is a **lifetime**
allowance, signed-in is a **daily** one, and you want to switch the
second on later without rework.

```python
WINDOW_LIFETIME = "lifetime"
WINDOW_DAY      = "day"

@dataclass(frozen=True)
class Entitlement:
    plan: str = PLAN_ANONYMOUS
    valuations: int | None = None      # None = no limit
    window: str = WINDOW_LIFETIME
    saves_limit: int | None = None
    watchlist_limit: int | None = None
    uid: str | None = None
    provider: str = "anonymous"

PLANS = {
    "anonymous": Entitlement(plan="anonymous", valuations=_env_int("ANON_VALUATION_LIMIT", 3),
                             window=WINDOW_LIFETIME),
    "free":      Entitlement(plan="free", valuations=_env_int("FREE_DAILY_VALUATIONS", None),
                             window=WINDOW_DAY),
    "unlimited": Entitlement(plan="unlimited", valuations=None, window=WINDOW_DAY),
}
```

Your later change is then a **Render dashboard edit**, not a deploy:
`FREE_DAILY_VALUATIONS=3`. Selling the paid plan is adding a billing
webhook that writes `subscriptions/{uid}` = `"unlimited"`, which
`resolve()` already reads. No rework, which was the requirement.

### 3.2 `identity.py` learns the provider

A Firebase ID token carries `firebase.sign_in_provider`: `anonymous` or
`google.com`. `Identity` gains `provider`, read from that claim, and
`resolve()` picks the plan from it. Four lines.

### 3.3 Linking costs nothing

`linkWithCredential` **keeps the same uid**. So the watchlist, the saved
valuations and the usage row are all already keyed correctly and nothing
needs migrating - the account simply stops being anonymous. On the next
token refresh `sign_in_provider` becomes `google.com` and the same uid
moves from the anonymous plan to the free plan.

One case does not resolve itself: linking to a Google account that
**already exists** throws `credential-already-in-use`. Then we sign in to
the existing account and the anonymous uid is orphaned. See the decision
at the end.

### 3.4 Where the counter lives — the one real decision

A limit the client can reach is not a limit, so the count must be written
by the server. But `identity.py` holds **no Firebase credential at all**,
on purpose, and says so in capitals:

> adding one server-side Firestore write would add a private key to
> Render. Think twice before doing that.

Three ways out.

| Option | Cost |
| --- | --- |
| **A. Firestore REST as the caller**, rules enforce monotonicity | No new secret. Rules question to verify (below) |
| B. Admin SDK + service-account key on Render | Simplest code; adds a key granting full Firestore access |
| C. Render Postgres / Key Value | Another service; free Postgres expires, free Key Value is not durable - bad for a lifetime counter |

**I recommend A.** The Firestore REST API accepts a **Firebase ID token**
as `Authorization: Bearer`, and requests carrying one are evaluated
against Security Rules. So the server verifies the token locally with the
code that already exists, then writes to Firestore *as that user*, and
the rules make the write safe:

```
match /usage/{uid} {
  allow read:   if request.auth.uid == uid;
  allow create: if request.auth.uid == uid && request.resource.data.lifetime == 1;
  allow update: if request.auth.uid == uid
                && request.resource.data.lifetime == resource.data.lifetime + 1;
  allow delete: if false;                      // or they reset by deleting
}
match /usage/{uid}/valued/{marker} {
  allow read:   if request.auth.uid == uid;
  allow create: if request.auth.uid == uid;
  allow update, delete: if false;              // create-only markers
}
```

The user can increment their own counter (self-harm only) and can never
lower or delete it. **Render gains no secret.**

**To verify before building:** whether Security Rules can reliably
validate the result of a numeric `increment` transform. If not, the
server does an explicit read-then-write of the concrete number inside a
Firestore REST transaction, which rules can validate. This is a half-hour
spike, not a design risk - but I would rather spike it than assert it.

### 3.5 What counts, and what does not

Server-side, with one mechanism rather than a list of special cases:
**one valuation per `(uid, ticker, UTC date)`**, recorded as a
create-only marker document.

```
usage/{uid}                      { lifetime: 2, day: "2026-11-25", dayCount: 2 }
usage/{uid}/valued/2026-11-25__AAPL      <- create-only
```

The server tries to create the marker. Firestore fails the create with
`ALREADY_EXISTS` if it is there, and **only a successful create
increments the aggregate**. That is atomic, needs no read-modify-write of
a set, and gives you every rule you asked for as a consequence:

| Action | Counts? | Why |
| --- | --- | --- |
| Value button, TRY chip, search result, new company | **yes** | first marker for that ticker today |
| Slider change | no | same ticker, same day - and this matters: `dcf_engine.dart` previews locally but *the backend reconfirms when the drag ends*, so without dedupe one drag would burn the allowance |
| Tab switch | no | no new ticker |
| Reopening the same company the same day | no | marker exists |
| Reopening a save | no | marker exists, and saves need no valuation call |
| Exporting a company already valued | no | `/excel` is not a valuation route |

Markers are tiny; prune anything older than 60 days, or leave them.

### 3.6 The API contract

Every valuation response carries what the app needs to render, and
nothing it needs to decide:

```json
"entitlement": { "plan": "anonymous", "window": "lifetime",
                 "limit": 3, "remaining": 1 }
```

Refusal is one status with a machine-readable reason:

```
403  { "error": "valuation_limit", "limit": 3, "window": "lifetime",
       "sign_in_lifts_limit": true }
```

The app shows the gate on that code and never on its own arithmetic.

**Public and unmetered, as asked:** `/health`, `/delete`, the
privacy/terms pages, `/search` and `/assumptions`. No token, no counting.

**If anonymous auth itself fails** - offline on first run, Firebase down -
there is no uid to count against. The request then falls back to the
existing per-IP rate limit and **is allowed**. A deliberate degradation:
Firebase having a bad day must not brick an app that works without an
account.

### 3.7 What the reader sees

- **While valuations remain**: a hairline pill in the header,
  `2 free valuations left`, at label weight. No badge, no colour that
  asks to be dealt with. Hidden entirely for signed-in users.
- **On the last one**: a `caution`-tinted card under the Value button.
  The only moment the count earns emphasis.
- **After the third**: the gate - "3 of 3 free valuations used", then
  *Carry on for free*, the sign-in button, and the reason a limit exists
  at all (each valuation pulls company filings and market prices).

**The gate is never a dead end.** Three states are drawn in the mockup:

- **ready** - sign in, or *Open something I already valued*, or Back.
- **sign-in failed** - "Nothing has changed", retry, and both ways out
  still present.
- **offline** - says so, confirms the three valuations are safe, and the
  saved ones still open.

Every state keeps a route that needs no network.

---

## 4. Play Store and privacy consequences

Anonymous auth means **every install now gets a Firebase user ID**, where
previously only signed-in users did. That is the change that matters, and
it is a Data safety change, not a code one.

### Data safety form

| Question | Before | After |
| --- | --- | --- |
| User IDs - collected | Yes, signed-in only | **Yes, every user** |
| User IDs - required or optional | Optional | **Required** (cannot value without a uid) |
| User IDs - purposes | App functionality | **App functionality + Fraud prevention, security and compliance** |
| App activity → App interactions | Not declared | **Declare: collected, not shared** (the per-ticker markers and counts are app interactions) |
| Email address | Collected, optional | unchanged - optional, sign-in only |
| Shared with third parties | No | No |
| Encrypted in transit | Yes | Yes |
| Users can request deletion | Yes | Yes |

Nothing becomes "shared". Nothing is processed ephemerally - it is stored,
so answer no to that.

### Privacy policy wording

`PRIVACY.md` and the hosted page need four additions:

1. **An anonymous account is created automatically.** On first use the app
   creates an anonymous Firebase account. It holds no name, no email and
   nothing that identifies a person; it exists so free valuations can be
   counted per installation rather than per IP address.
2. **What the counter holds**: the account identifier, how many
   valuations have been made, the dates, and which tickers were valued on
   which day. Why: fair use, and so one client cannot exhaust a shared
   rate limit for everyone.
3. **Signing in links, it does not replace.** The anonymous identifier
   becomes the identifier of the Google account. Nothing is lost and
   nothing new is created.
4. **Retention**: markers pruned after 60 days; the whole row is removed
   with the account, by the existing in-app deletion and `/delete`.

### App access, for reviewers

Currently this is presumably "all functionality available without special
access". That is no longer true: the fourth valuation is behind a
Google sign-in, and Play requires working credentials for anything behind
a login.

Add under **App content → App access**:

> Valuing companies needs no account. The first three valuations are free
> and require no sign-in. The fourth shows a sign-in screen. To review it:
> value any three companies, then a fourth - the gate appears - and sign
> in with the account below.

**You need to create a dedicated test Google account for this.** I will
not generate or handle its password; put it straight into the Play
Console.

### One thing for later, not now

You said no billing. When the paid unlimited plan arrives, Play Billing
is mandatory for digital content sold in-app - a sign-in gate is not a
paywall and needs none of that, but the `unlimited` plan will.

---

## Accepted weaknesses, recorded on purpose

Three ways a determined person gets more than three free valuations. All
three are known, none is fixed, and the reason is the same each time: the
alternative is a device fingerprint, which is a far worse thing to put in
a privacy policy than a limit somebody can dodge.

1. **Reinstalling the app, or a private browser window.** A new anonymous
   account, a new uid, a new three. This is the one we set out to accept.

2. **Deleting the counter document.** The rule is
   `allow delete: if owner(uid) && !existsAfter(users/$(uid))`, and an
   anonymous installation has no `users/{uid}` record - so the branch that
   stops a signed-in reader resetting their own count does not stop an
   anonymous one. A script with the app's own credentials can therefore
   reset without reinstalling.

   Accepted, because it reaches exactly the same place as (1) by a shorter
   route, and because the two real defences are not the rule: App Check,
   which a script cannot produce a token for, and the per-IP daily cap,
   which limits how often any of this is worth doing.

   **This changes the day `FREE_DAILY_VALUATIONS` is set.** A signed-in
   reader who deletes `users/{uid}` and lets the app recreate it would get
   the same reset, and at that point the counter's delete rule needs to be
   narrower - most likely a `deleted_at` tombstone the app may write but
   not remove.

3. **Many installations behind one address**, up to `ANON_IP_DAILY_CAP`.
   Deliberate: the cap is set where a household or a small office will
   never reach it.

## Decisions, as taken

All five were answered on 2026-11-25 and are built as described.

| | Decision |
| --- | --- |
| Counter store | **A — Firestore as the caller.** No new secret on Render. Plus two security fixes: the marker and the +1 are bound to each other with `getAfter`/`existsAfter` so a client cannot pre-create markers, and the counter is deletable only in a write that leaves no user record behind |
| `credential-already-in-use` | Sign in to the existing account, discard the anonymous uid, **delete its counter first** — while still signed in as it, because afterwards the rules make it unreachable |
| Counter pill | In the header, as drawn |
| "Valuations this month" | Kept, as **Valuations counted** |
| Google mark | Official asset bundled from their branding guidelines, unaltered |

Abuse protection, also as instructed: reinstalling resets the allowance
and that is accepted; no device identifiers; Firebase App Check (Play
Integrity on Android, reCAPTCHA Enterprise on web) verified by the API;
and a per-IP daily cap on signed-out valuations, default 20,
configurable.

Limits are configuration: anonymous lifetime = 3, signed-in daily = none.
`FREE_DAILY_VALUATIONS=3` in the Render dashboard is the whole of the
change you described wanting later, and `test_entitlement.py` asserts
exactly that.
