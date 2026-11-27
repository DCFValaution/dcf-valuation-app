# Privacy

The canonical text. The in-app sheet (`showPrivacySheet` in
`app/lib/main.dart`) and the published policy page are both this, so they
cannot drift apart.

The Firestore location is `asia-southeast1` (Singapore), chosen when the
project was created. It cannot be changed afterwards, which is why the
policy names it rather than saying "our servers".

---

## What changed, and why the old text could not stand

The first line of the previous policy was:

> Intrinsic has no accounts, no sign-in and no database. We do not ask who
> you are and have no way to know.

Optional accounts make all four clauses false. A policy that is false in
its opening sentence is worse than no policy, so this is a rewrite of that
paragraph and an addition of four more — not a footnote.

Everything the old policy promised about people who **do not** sign in is
unchanged and still true, and the new text says so explicitly rather than
leaving a reader to infer it.

---

## The policy

**Accounts are optional, and everything works without one.** You can value
any company, change any assumption and export the spreadsheet without
signing in, and we will not know you were here. Signing in is offered in
one place only: when you try to save a company to a watchlist or save a
valuation. You can decline and carry on.

**If you do not sign in,** the app creates an anonymous account for your
installation the first time it opens. This is new, and it is the one
place where less is now true than before, so it is stated plainly rather
than buried.

An anonymous account holds no name, no email address and nothing that
identifies you as a person. It is a random identifier belonging to that
copy of the app on that device. We use it for exactly one thing: counting
the free valuations, so that the first three can be free per installation
rather than per network address — and a network address is shared by a
household, an office or a whole campus, so counting that way would have
meant one person using up everybody else's.

**What an anonymous installation stores, in full:** the identifier, how
many valuations have been made, the dates, and which company was valued on
which day. That last one exists so that reopening the same company,
dragging a slider or switching between views does not count again. Nothing
else: no watchlist, no saved valuations — those need an account, and the
database rules refuse them to an anonymous one rather than merely not
offering them.

Reinstalling the app, or clearing its data, ends that anonymous account
and starts a new one. We cannot connect the two, and we do not try: there
is deliberately no device fingerprint here.

**If you sign in,** you sign in with Google. We never see or hold your
password — Google verifies you and gives us a token. From that token we
keep two things: the account identifier Google assigns you, and your email
address. We do not keep your name, your profile photo, your language or
anything else Google offers us.

**What we then store, in full:**

| | |
|---|---|
| Your Google account identifier | So your saved work can be found again |
| Your email address | So the account screen can show whose account it is |
| Your watchlist | The ticker symbols you added, and each one's last value and price as your own device last saw them |
| Your saved valuations | The name you gave each one and the assumptions you set |
| Two timestamps | When the account was created, and when it was last used |
| Your valuation count | How many valuations have been made, the dates, and which company on which day — the same counter an anonymous installation has, which simply carries on being yours after you sign in |

That is the whole list. We do not store prices, filings, computed
valuations beyond the one figure kept for comparison, your IP address
against your account, any device identifier, or any record of what you
looked at.

**Signing in links, it does not start again.** When you sign in to an
anonymous installation, the identifier it already had BECOMES your
account's identifier. Nothing new is created and nothing is copied across,
which is why the valuations you made before signing in are already
counted as yours afterwards.

The one exception is signing in to a Google account you already had with
us. There is nothing to link in that case — you simply sign in to the
account you have — and the anonymous identifier and its counter are
deleted at that moment.

**Where it is held.** Your account and saved work are stored in Google
Firestore, in Singapore (Google Cloud region `asia-southeast1`). Google processes it on our behalf and under
our instructions; it is not used for Google's own purposes, and it is not
shared with anyone else. We do not sell data, and there is nobody to sell
it to.

**No tracking, as before.** No cookies. No analytics — including no Google
Analytics, which was deliberately left switched off when the Firebase
project was created. No advertising, no tracking of any kind, no marketing
email. Your data is never used to train any AI model.

**Deleting your account.** Open Account and choose *Delete account and all
data*. You can also do it without the app, at
`https://intrinsic-web.onrender.com/delete`. Either removes your account,
your watchlist, your saved valuations and your valuation counter. They disappear from the app
immediately and are gone from our systems, including backups, within 30
days. It cannot be undone, and the app keeps working afterwards — signed
out, exactly as it does for someone who never signed in.

**How long the counter is kept.** The per-day, per-company records are
removed after 60 days; the counter itself lasts as long as the account
does and goes with it when the account is deleted. An anonymous
installation's counter is deleted when you sign in to an account you
already had, and otherwise simply ends when the installation does.

**A daily count per network, held as a hash.** Alongside the per-
installation allowance there is a cap on how many signed-out valuations
come from one network address in a day. To count them we need to tell two
addresses apart; we do not need to know what they are. So the address is
never stored: it is converted to an unreversible hash using a random key
that exists only in the server's memory and is thrown away whenever the
server restarts. The hashes are held in memory only, never written to
disk, reset every day, and cannot be matched against any address or
against each other after a restart. Signing in removes you from this
count entirely.

**Checking that the app is the app.** Requests carry an attestation from
Google's Firebase App Check, which tells our server that the request came
from a genuine copy of this app rather than from a script. On Android this
uses Google Play Integrity and on the web Google reCAPTCHA Enterprise;
both report on the app and the device, not on you, and neither sets a
cookie or tracks you between sites. It exists because the free allowance
would otherwise be trivial to take at scale.

**Deleting the app does not delete your data.** Uninstalling removes the
app and the signed-in session from your device; the copy on our servers
stays until you ask for it to be deleted. If you have uninstalled the app
and want your data gone, use the web link above.

**Getting a copy.** Account → *Download my data* gives you everything we
hold about you as a JSON file. There is no form and no wait.

**Where the figures come from.** Company financials come from a
third-party market data provider; we request them from our own server, so
your device never contacts them and they never see you. The web version
loads its graphics engine and the Roboto font from Google's content
network, which means Google's servers see your IP address when the page
loads, as they would for any site using those files. They set no cookies.

**Hosting and logs.** The app and its server run on Render. Like any web
host, Render keeps standard server logs, which include IP addresses. Those
logs are not linked to any account.

---

## Notes for the data-safety form

Not part of the published policy; kept here so the two cannot disagree.

**THE ANSWERS CHANGED WHEN ANONYMOUS ACCOUNTS LANDED.** Before, a reader
who never signed in gave us nothing, so every row was "optional". That is
no longer true: an anonymous identifier is created for every installation
on first launch, and the free-valuation counter is kept against it. The
rows below are the current answers, with the changed ones marked.

| Play Console question | Answer | Because |
|---|---|---|
| **User IDs** | Collected · not shared · **required** · linked to identity | **CHANGED.** Every installation gets an anonymous Firebase identifier, so this is no longer avoidable by not signing in. Purposes: *App functionality* AND *Fraud prevention, security and compliance* — the second because the counter and App Check exist to stop the free allowance being taken at scale |
| **App activity → App interactions** | Collected · not shared · **required** | **NEW.** How many valuations were made, on which dates, and which company on which day. Purposes: *App functionality* and *Fraud prevention, security and compliance* |
| Email address | Collected · not shared · **optional** · linked | Only on signing in. Account management, app functionality |
| Other user-generated content | Collected · not shared · optional | The watchlist and the saved valuations; an anonymous installation cannot have either |
| Approximate location / IP | **Not collected** | The per-network daily cap keeps an unreversible hash of the address in memory, keyed by a random value discarded on restart, never written to disk and reset daily. Render's own server logs do contain addresses, as any web host's do, and are not linked to an account |
| Any data used for tracking | **No** | No analytics SDK. App Check reports on the app and the device, not on a person, and is not used to track across apps or sites |
| Data encrypted in transit | **Yes** | HTTPS throughout; Firestore's own transport |
| Data processed ephemerally | **No** | The counter is stored, not computed and discarded |
| Users can request deletion | **Yes** | In-app, plus `https://intrinsic-web.onrender.com/delete`; both remove the counter too |
| Data collection is optional | **No** | **CHANGED.** The anonymous identifier is not optional, because the app cannot offer a per-installation free allowance without one |

Nothing became "shared". Nothing is sold, and there is still no analytics
SDK, no advertising identifier and no tracking.

---

## App access, for Play reviewers

**This answer changed too.** It used to be "all functionality is available
without special access". The fourth valuation is now behind a Google
sign-in, and Play requires working credentials for anything behind a
login.

Under **App content → App access**, add an instruction set:

> Name: Free valuation limit
>
> Valuing companies needs no account. The first three valuations are free
> and require no sign-in at all. The fourth shows a sign-in screen.
>
> To review it: value any three companies, then start a fourth — the
> sign-in screen appears — and sign in with the Google account below.

**You need a dedicated test Google account for this.** Create it yourself
and type its credentials straight into the Play Console; they are not in
this repository and must not be.
