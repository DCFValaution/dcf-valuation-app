# Play Store prep

What has to be true before the first public release, and what has to be
done again after it. Kept here rather than in a head, because several of
these are things that fail silently months later.

---

## Before the first upload

- [ ] **Compile for every platform, not just the one you were looking
      at.** `python tools/preflight.py` runs the Python tests, the
      analyzer, the Dart suite, and then a compile for Android AND for
      the web.
      ```powershell
      python tools\preflight.py
      ```
      The two compiles are not redundant, and this is measured rather
      than assumed. The same undefined name planted on each side of the
      conditional export in `excel_delivery.dart`:

      | planted in | `dart analyze` | compile for Android |
      | --- | --- | --- |
      | `excel_delivery_io.dart` (Android/iOS) | caught | caught |
      | `excel_delivery_web.dart` | caught | **blind to it** |

      So one platform's compile says nothing about the other's. The
      analyzer does catch plain errors in both files - what it cannot do
      is run a toolchain, which is the failure that actually happened:
      the web bundle built and ran while `flutter run` on Android would
      not compile, because the Android build state was stale rather than
      the source wrong. No analyzer would ever have caught that. See
      "When Android will not compile" below.

      The Android step is a kernel build, not an APK: same front end,
      same errors, 48s against 342s.

      The FIRST check is not a compile at all, because the worst of
      these failures compiles perfectly: it confirms that
      `firebase_core`, `firebase_auth`, `cloud_firestore` and
      `google_sign_in_android` are in the Android build. See "When
      Android compiles and still will not work" below.
- [ ] **Turn App Check enforcement on — LAST, not first.** Play
      Integrity for Android, reCAPTCHA Enterprise for web, then the
      Firestore toggle and `APP_CHECK_ENFORCED=1` on Render. Most of the
      Android half cannot be done until after the first Play upload,
      because it needs the SHA-256 of the *app signing* key. Full steps,
      in order, under "Turning App Check on" below.

      It is last because it is a hard cutover: the moment it is on, any
      client that cannot produce a token is refused, including a copy of
      the app somebody has not updated. Watch the request metrics until
      "outdated client" is near zero before flipping it.
- [ ] **Set `FIREBASE_PROJECT_NUMBER` on Render.** The long numeric id
      from `google-services.json`. Not a secret - it ships inside the
      app - but without it the API cannot verify an App Check token at
      all, and says so in the log on every request.
- [ ] **Deploy the Firestore rules.** They are NOT deployed by pushing
      code — a new project starts on `allow read, write: if false`, which
      denies every read and write while the app looks merely broken.
      ```
      cd firebase
      npx firebase deploy --only firestore:rules
      ```
      Then confirm in Firebase console → Firestore → Rules that what is
      live matches `firebase/firestore.rules`. Do this again after ANY
      change to that file; `npm test` passing proves the rules are
      correct, not that they are deployed.
- [ ] **Register the Play App Signing fingerprints in Firebase.** *See the
      section below — this one bites after release, not before.*
- [ ] `google-services.json` in `app/android/app/` contains an Android
      OAuth client (`client_type: 1`). Check with:
      ```powershell
      python -c "import json; d=json.load(open('app/android/app/google-services.json')); print([o['client_type'] for o in d['client'][0]['oauth_client']])"
      ```
      A list without a `1` means the file was downloaded before the SHA
      fingerprints were added, and Google sign-in will fail on Android
      with an error that does not say so.
- [ ] Firebase API keys restricted in the Google Cloud console (Android
      key to this package + fingerprints, web key to the site's origins).
- [ ] Privacy policy published at a public URL, matching `PRIVACY.md`.
- [ ] Account deletion reachable **both** in-app and at
      `https://intrinsic-web.onrender.com/delete`, with the web URL
      entered in Play Console → App content → Data safety.
- [ ] Data safety form answered per the table at the end of `PRIVACY.md`.
      **The answers changed with the free-valuation limit** and three rows
      are new or different:

      | Row | Answer |
      | --- | --- |
      | User IDs | Collected · **required** (was optional) — every install gets an anonymous Firebase id. Purposes: App functionality **and** Fraud prevention, security and compliance |
      | App activity → App interactions | **NEW** — collected, not shared, required. The valuation counts and the per-day, per-company markers |
      | Approximate location / IP | **Not collected.** The per-network daily cap keeps only an unreversible hash of the address, in memory, keyed by a random value discarded on restart, never written to disk, reset daily. Render's own host logs contain addresses as any host's do, and are not linked to an account |

      "Data collection is optional" also flips to **No**: the anonymous
      identifier cannot be declined, because a per-installation free
      allowance is not possible without one.
- [ ] `flutter build appbundle --release` succeeds and is signed by the
      upload key (`key.properties` present).

---

## Shipping the free-valuation limit, in order

Three things change together - the Firestore rules, the API, and the app -
and they must go out in this order. Each step is safe to sit in on its own
for as long as you like; the orders that are NOT safe are called out
underneath.

### 1. Firestore rules — FIRST, and before anything else

```powershell
cd firebase; npm test
cd firebase; npx firebase deploy --only firestore:rules
```

Run the suite first. It takes about ten seconds, needs no Firebase account
(the emulator serves a fake project), and it is the only thing standing
between one reader's saved work and everybody else's.

**Why first.** The rules add `usage/{uid}` and its markers. Until they are
deployed, every write to those paths is denied by the default-deny rule at
the bottom of the file - so:

- the API cannot count anything. It degrades rather than fails: the
  valuation is served, `X-Valuations-Degraded: 1` comes back, the app
  hides the pill, and the per-IP cap is the only limit in force. Nobody
  is refused, and nothing is lost except the counting.
- the app's **account deletion would fail**, because it now deletes
  `users/{uid}` and `usage/{uid}` in one batch and the batch is atomic.
  That is the one genuinely bad outcome, and it is why the rules go first
  rather than merely early.

The rules are also backwards-compatible: deploying them changes nothing
for the app that is live today, which touches none of those paths.

### 2. The API — SECOND, with App Check enforcement OFF

Push to `main`; Render redeploys. Before or with it, set in the Render
dashboard:

| Variable | Value | Why |
| --- | --- | --- |
| `FIREBASE_PROJECT_ID` | `intrinsic-e3abf` | **Was never actually set.** Without it `identity.verify` cannot run, every caller is unattributable, nobody is counted and every response is degraded — and the app keeps working, which is why it went unnoticed |
| `FIREBASE_PROJECT_NUMBER` | the long numeric id | New. Without it no App Check token can be verified, and the log says so on every request |
| `APP_CHECK_ENFORCED` | **leave unset** | **Must stay off.** See below |
| `ANON_VALUATION_LIMIT` | unset (defaults to 3) | Set it only to change the number |
| `FREE_DAILY_VALUATIONS` | **leave unset** | Signed-in readers stay unmetered for now |
| `ANON_IP_DAILY_CAP` | unset (defaults to 20) | |

**`APP_CHECK_ENFORCED` must be off at this point, and for some time
after.** The app that sends an App Check header does not exist in anyone's
hands yet. Turning enforcement on now would refuse every request from
every reader on the current version. The full rollout, including the
Firebase console half, is under "Turning App Check on" below - do it days
after step 3, not minutes.

**Why the API goes before the app.** A new app against an old API is the
bad combination: the app would ask for an anonymous account, send its
tokens, and get no `X-Valuations-*` headers back - so the pill would never
appear, the gate would never appear, and the limit would silently not
exist. The reverse is harmless: the API that is live after step 2 sees the
current app send no ID token, treats it as unattributable, and governs it
with the per-IP cap exactly as it does today.

### 3. The app — LAST, web and Play together

The web build needs the reCAPTCHA site key once you have one (step 2 of
"Turning App Check on"); without it App Check simply does not activate on
the web, which is fine until enforcement is on.

```powershell
cd app; flutter build web --release --dart-define=RECAPTCHA_SITE_KEY=<key>
```

**Check the Anonymous provider is enabled** in Firebase console →
Authentication → Sign-in method → Anonymous, before this build reaches
anybody. Without it, `signInAnonymously()` fails, every reader is
unattributable, and the free allowance quietly becomes the per-IP cap for
everyone behind one address. The app still works - it is written to
degrade rather than break - but the feature does not.

### The web deploy must precede the Play submission

Google checks the deletion URL you give them. `https://intrinsic-web.onrender.com/delete`
returns 200 today and renders the **home screen**, because the route has
never been deployed - the live `main.dart.js` contains no reference to it
at all. A reviewer following that link would see an ordinary app with no
way to delete anything.

So: deploy the web app (step 3), then **open the link in a real browser
and confirm the deletion page actually appears** - a 200 is not evidence,
because the single-page rewrite returns 200 for every path including ones
that do not exist. Only then submit to Play.

### What NOT to do

| Order | What happens |
| --- | --- |
| App before rules | Account deletion fails outright - the atomic batch is denied |
| App before API | The limit silently does not exist: no pill, no gate, nobody counted |
| App Check enforced before the app has shipped and spread | Every reader on the old version is refused, immediately |
| Submitting to Play before the web deploy | The deletion URL Google checks renders the home screen, because the route is not deployed |
| `FREE_DAILY_VALUATIONS` set on day one | Signed-in readers get metered before anyone has had a chance to notice the limit exists |

---

## Turning App Check on

LEAVE THIS UNTIL LAST, and do it in this order. App Check enforcement is
a hard cutover: the moment it is on, every client that cannot produce a
token is refused, including the copy of the app on somebody's phone that
has not updated yet. The code ships long before the switch is flipped,
which is why `APP_CHECK_ENFORCED` defaults to off and the Firebase
console has a monitoring mode.

### 1. Android: Play Integrity

Play Integrity attests that the app is a genuine build from Play on a
genuine device. It needs the signing certificate Play will actually use,
so **most of this has to wait until after the first upload** - Play App
Signing generates its own key and the SHA-256 of that key is not knowable
beforehand.

1. Upload the first release to the Play Console (internal testing is
   enough).
2. Play Console → your app → **Test and release → Setup → App signing**.
   Copy the **SHA-256 certificate fingerprint** of the *app signing key*,
   not the upload key.
3. Firebase console → **Project settings → General → Your apps →
   Android** → **Add fingerprint** → paste it.
4. Firebase console → **Project settings → Integrations → Google Play** →
   **Link**. Play Integrity will not verify without the link.
5. Firebase console → **App Check → Apps** → the Android app → **Play
   Integrity** → **Register**. Leave the token TTL at the default.

### 2. Web: reCAPTCHA Enterprise

1. Google Cloud console (same project) → **Security → reCAPTCHA
   Enterprise** → **Create key**.
   - Platform: **Website**
   - Domains: `intrinsic-web.onrender.com` (add any custom domain too)
   - Type: **Score-based**, so no visitor is ever shown a puzzle
2. Copy the **site key**.
3. Firebase console → **App Check → Apps** → the web app → **reCAPTCHA
   Enterprise** → **Register** → paste the site key.
4. Put the same site key in the web build:
   `--dart-define=RECAPTCHA_SITE_KEY=<key>`. It is a public key - it ships
   in the page either way - but it is environment-specific, so it is a
   define rather than a constant.

### 3. Watch before enforcing

Firebase console → **App Check → APIs → Cloud Firestore → Request
metrics**. Three numbers: verified, unverified, and unverified from an
outdated client. **Wait until "outdated client" is near zero**, which
means waiting for people to update. Days, not minutes.

### 4. Enforce, in two places

1. Firebase console → **App Check → APIs → Cloud Firestore → Enforce**.
2. Render dashboard → `dcf-valuation-api` → Environment →
   `APP_CHECK_ENFORCED` = `1`.

The API's own Firestore writes are made with the caller's ID token AND
the caller's App Check token, forwarded from the app, so enforcing
Firestore does NOT lock the backend out. That is the whole reason the app
sends the header to us as well as to Google.

### Also set, once

Render dashboard → `FIREBASE_PROJECT_NUMBER` - the long numeric id from
`google-services.json` (`project_number`). Not a secret; it ships inside
the app. Without it the API cannot verify a single App Check token, and
logs a warning saying so on every request.

### Developing after enforcement is on

An emulator or a debug build cannot attest. Firebase console → **App
Check → Apps → ⋮ → Manage debug tokens**, and register the token the app
prints at startup in debug. Never register a debug token for a build that
ships.

---

## When Android compiles and still will not work

The failure with no compile-time symptom at all, and the one that cost
the most time: the app builds, installs, launches, values a company -
and tapping Save says **"Signing in is unavailable just now"**, which
names no cause.

What it means is that `Firebase.initializeApp` threw. In the logs:

```
PlatformException(channel-error, Unable to establish connection on
channel: "...FirebaseCoreHostApi.initializeCore")
```

"Unable to establish connection on channel" means the plugin's ANDROID
half is not in the app: the Dart calls a method channel with nobody on
the other end. Not a bad `google-services.json`, not a missing
`serverClientId`, not an emulator without Play services - all three were
checked and all three were fine.

The cause is `app/.flutter-plugins-dependencies` losing entries. The
registrant is generated from it on every build, so the build faithfully
produces an APK with the Firebase plugins left out, and nothing complains
until the device runs it.

```powershell
cd app; flutter pub get
```

Then rebuild.

### The build itself now refuses

`preflight.py` checks this first, before any compile - but nobody runs
preflight before `flutter run`, and that is the command this failure
arrives on. So the assertion also lives in
`app/android/settings.gradle.kts`, which Gradle evaluates while it is
still reading settings: before a line is compiled, and long before
anything is installed.

A stripped build now fails in about 5 seconds:

```
FAILURE: Build failed with an exception.
* Where:
Settings file 'app\android\settings.gradle.kts' line: 73
* What went wrong:
.flutter-plugins-dependencies lists no Android build for:
  - cloud_firestore - without it, no watchlist and no saved valuations
  - firebase_auth - without it, nobody can sign in
  - firebase_core - without it, Firebase will not start at all
  - google_sign_in_android - without it, the Android sign-in sheet never opens
Fix: cd app && flutter pub get   (then build again)
```

Against 130s of compiling, 75s of installing, and an app that looks fine
until someone taps Save. The required list is in two places on purpose -
`settings.gradle.kts` and `tools/preflight.py` - and a plugin added later
belongs in both.

`GeneratedPluginRegistrant.java` is NOT the thing to check - it is
regenerated by every build, so it repairs itself and tells you nothing.

To confirm it on a device rather than by inference:

```powershell
adb logcat -d -s flutter:V
```

Silence at startup is the good outcome; the line to look for is
"Firebase did not start", which is logged in release builds too.

### What is still not known about it

Three occurrences. On the third the evidence was captured before
`flutter pub get` erased it, which settled what the failure *is* without
settling what causes it.

**What it is.** Flutter's plugin discovery stopped partway. Run
`flutter run -v` and it logs one `Found plugin <name> at <path>` line per
plugin, 21 of them, in a fixed order. The stripped file's
`dependencyGraph` holds exactly the first 11 of that sequence and nothing
from the 12th on. It is a truncation, not a filter and not a bad input:

```
 1 share_plus            <- kept
 ...
11 jni                   <- kept
12 google_sign_in        <- gone
...
21 cloud_firestore_web   <- gone
```

The same 11 appeared both times, byte for byte, so the cut-off is
reproducible in its result even though the failure is not reproducible on
demand. Two other descriptions of that same set are true and both are
coincidence - "everything extracted into the pub cache on 2026-11-23
21:27" and "every plugin package alphabetically before `jni`" - so
neither is worth chasing.

**What it is not.** Each ruled out by inspection, not by argument:

- the pub cache - every dropped package intact, `pubspec.yaml` present
  and declaring `android:`, no reparse points, no case-sensitivity flag,
  `Test-Path` true for each one from PowerShell;
- `package_config.json` and `package_graph.json` - the copy saved at the
  moment of the strip lists all nine root dependencies including all
  three Firebase packages, and only one such pair exists on disk;
- a stale resolution - there is no second `.dart_tool` anywhere under the
  project;
- `pub get` running implicitly - `package_config.json` is older than
  `pubspec.yaml` is not true here, so `flutter run` has no reason to, and
  the archived mtimes confirm it did not;
- the environment - one SDK, and `PUB_CACHE`, `FLUTTER_ROOT` and
  `JAVA_HOME` unset at User, Machine and Process scope, with no
  PowerShell profile on the machine to set them at runtime;
- the shell, the starting directory and the command shape - replayed
  through `powershell.exe` 5.1 starting in `C:\Users\User` with
  `cd ...\app;` on the same line, which is how it is typed when it
  fails. Produced all 21;
- `flutter emulators --launch <wrong name>` beforehand - a no-op, exit 0,
  file untouched.

So: unproven. It has never happened in an automated run and has happened
three times in a hand-typed window, on a sample far too small to mean
anything.

**Which is why the guard archives.** `flutter pub get` is the fix and
also destroys every file that could identify the cause, so the check in
`settings.gradle.kts` copies them first, to
`strip-evidence/<timestamp>/`, before it fails:
`.flutter-plugins-dependencies`, `package_graph.json`,
`package_config.json`, `pubspec.yaml`, `pubspec.lock`, and an
`mtimes.txt` recording modification times - which is the only way to tell
afterwards whether pub ran. Nobody has to remember to do it. The next
occurrence documents itself.

## When `flutter run` exits instead of attaching

`flutter run` builds, installs, prints `FlutterJNI: flutter was loaded
normally`, and then returns to the prompt with no "Syncing files" and no
key commands - while the app stays open on the device. That is not a
plugin problem and not a compile problem. In logcat:

```
W ActivityManager: Process ...com.intrinsic.app failed to attach
I ActivityManager: Killing ... (adj -10000): start timeout
```

The app did not reach the system within the launch timeout, so Android
killed that process; `flutter run` lost the thing it was waiting for and
gave up. The usual cause is an emulator too slow to start a debug APK -
in particular one launched with software rendering:

```powershell
emulator -avd <name> -gpu swiftshader_indirect
```

Confirm how the running emulator was launched before blaming the app -
the AVD name alone does not tell you, since the same AVD boots either
way:

```powershell
adb -s emulator-5554 emu avd name
```

A hardware-GPU emulator attaches; a swiftshader one frequently does not.
Nothing about the build changes - the same APK runs fine once it is
launched by hand, which is what makes this look like an app fault.

---

## When Android will not compile

Two stale-state failures, neither of which is a bug in the source, and
both of which look like one.

**"The getter 'X' isn't defined for the type 'Y'"**, where `X` is a name
imported from a package - `FieldValue`, `SetOptions`, anything. Note the
wording: when the compiler cannot resolve the package, it stops seeing
the name as top-level and reads it as an implicit `this.X`, which is why
the error names YOUR class. A genuinely missing import says "undefined
name" instead. The package resolution is stale:

```powershell
cd app; Remove-Item .flutter-plugins-dependencies -ErrorAction SilentlyContinue; flutter pub get
```

**"Plugin directory does not exist: ...\<package>-<version>\android"**,
naming a directory that is plainly there. A stale Gradle daemon:

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
cd app\android; .\gradlew.bat --stop
```

`gradlew` needs `JAVA_HOME`; it is not on PATH, though Flutter finds the
JDK by itself. Do not go looking for the fault in the Dart until both of
these have been cleared and the build has been run again.

---

## Play App Signing: the fingerprint that only exists after upload

**This is the one that fails silently after release.**

When Play App Signing is enabled — it is, for every new app — Google
re-signs your bundle with a key **it** holds. The certificate users
actually get is therefore *not* your upload certificate, and its
fingerprint is one you cannot compute locally: it does not exist until
Google generates it.

Firebase only knows the fingerprints you registered by hand. So the chain
is:

1. Debug build → signed by the debug key → works, because that fingerprint
   was registered.
2. Internal testing from a bundle you uploaded → signed by **Google's app
   signing key** → Google sign-in **fails**, because Firebase has never
   seen that fingerprint.

The failure looks like nothing: the Google sheet opens, the account is
picked, the sheet closes, and the app is still signed out. No error, no
log line that names the cause.

**What to do, after the first upload and before any tester installs it:**

1. Play Console → your app → **Test and release → Setup → App signing**.
2. Under **App signing key certificate**, copy the **SHA-1** and
   **SHA-256**.
3. Firebase console → Project settings → Your apps → the Android app →
   **Add fingerprint**, once for each.
4. **Download `google-services.json` again** and replace the one in
   `app/android/app/`. The file has to be the one that contains the new
   OAuth client, and the next release has to be built with it.

Repeat step 2–4 if you ever reset the upload key, or if Play issues a new
app signing key.

---

## Restricting the Firebase API keys

A Firebase `apiKey` is not a credential — it ships in every binary and
every web bundle, and is public by design. What stops someone else pointing
their own app at your project is (a) the Firestore rules, which are tested,
and (b) restricting the key to the callers that are allowed to use it.

### What uses each key, before you restrict anything

| Key | Lives in | Used by |
| --- | --- | --- |
| **Android key** `AIzaSyDKJk4ipi…` | `app/android/app/google-services.json`, and the `android` block of `app/lib/firebase_options.dart` | The Android app only — the release build from Play, and every debug build on an emulator or device |
| **Browser key** `AIzaSyDoTHjAPX…` | the `web` block of `app/lib/firebase_options.dart` | The web app, **and Firebase Auth's own hosted sign-in handler** |

**The Render backend sends no API key at all, so nothing you do here can
break it.** Checked rather than assumed:

- `identity.py` and `app_check.py` verify tokens against Google's PUBLIC
  JWKS endpoints, which take no key.
- `usage.py` authenticates to the Firestore REST API with **the caller's
  own ID token**.

That is the point of this backend holding no Firebase credential, and it
pays off here: the only things a bad restriction can break are the two
apps.

In the **Google Cloud console** (not Firebase) →
**APIs & Services → Credentials**, under *API keys* there are two auto-created
keys: *Android key (auto created by Firebase)* and *Browser key (auto created
by Firebase)*.

**Android key**

1. Open it → **Application restrictions → Android apps**.
2. **Add an item** for each certificate: package name `com.intrinsic.app`
   plus the **SHA-1**.

   SHA-1 here, SHA-256 in Firebase for App Check and Play Integrity. Two
   different fields wanting two different digests of the same key; pasting
   one into the other fails in a way that looks like a wrong key.

   Add one per fingerprint:
   - debug SHA-1
   - upload/release SHA-1
   - **Play App Signing SHA-1** (once it exists — see above)
3. **API restrictions → Restrict key**, and select only:
   *Identity Toolkit API* (sign-in, including anonymous),
   *Token Service API* (refreshing ID tokens — without it sessions die
   after an hour), *Cloud Firestore API*, *Firebase Installations API*
   (the SDKs call it on startup; omitting it breaks App Check and
   sometimes Auth), and *Firebase App Check API* once App Check is on.

   Not needed, and worth leaving out: Firebase Management, Cloud Storage,
   Cloud Functions, Cloud Messaging. None is used.
4. Save. Changes take a few minutes to apply.

**Browser key**

1. Open it → **Application restrictions → Websites**.
2. **Add** each origin that serves the app:
   - `https://intrinsic-web.onrender.com/*`
   - `https://intrinsic-e3abf.firebaseapp.com/*` — **do not leave this
     out.** Google sign-in on the web round-trips through Firebase Auth's
     own hosted handler at `<project>.firebaseapp.com`. Restrict the key
     to your own domain alone and sign-in fails on the web, with an error
     that says nothing about referrers.
   - `http://localhost:*/*` and `http://127.0.0.1:*/*` — while developing.
     The port matters: `http://localhost/*` does not match
     `http://localhost:8090`. Remove both before release, or a page on any
     localhost can use the key.
   - Any custom domain, the day you add one.
3. **API restrictions → Restrict key**, same APIs as above.
4. Save.

**Check you have not locked yourself out:** after the propagation delay,
sign in on the web app and on a debug Android build. A restriction that is
too tight fails exactly like a wrong fingerprint — the sheet closes and
nothing happens — so test both before shipping.

---

## After every release

- [ ] Confirm the deployed commit matches `HEAD` (`/health` returns it).
- [ ] Sign in and load the watchlist once against the real project. A
      rules deployment that was forgotten shows up as "Could not load
      your watchlist" with a retry, not as a crash, so nothing else will
      tell you.
- [ ] Sign in on a Play-installed build, not just a local one. This is the
      only way to catch a missing Play App Signing fingerprint.
