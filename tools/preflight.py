"""Every check that has to pass before a release, including the ones a
single-platform build cannot make.

WHY THIS EXISTS
---------------
`dart analyze` resolves one branch of a conditional import and compiles
nothing. `flutter build web` compiles the WEB branch only. So a release
could be analysed clean, tested green, built for the web, shipped - and
still fail on Android, which is the platform most of the readers are on.

That is not hypothetical. It happened twice in one afternoon: once where
the web bundle built and ran while `flutter run -d emulator-5554` would
not compile at all, and once where every platform compiled and the
Android app still could not start Firebase, because a plugin had dropped
out of the build. The second kind is why the first check here is not a
compile.

    python tools/preflight.py            # everything
    python tools/preflight.py --quick    # skip the two slow ones

Exit code is the number of failed checks, so it is usable from a hook.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


class Check:
    """A command to run, or a function to call. One of the two."""

    def __init__(self, name: str, why: str, cmd: list[str] | None = None,
                 cwd: Path = ROOT, slow: bool = False, fn=None) -> None:
        self.name = name
        self.why = why
        self.cmd = cmd
        self.cwd = cwd
        self.slow = slow
        self.fn = fn


def android_plugins_are_present() -> tuple[bool, str]:
    """The plugins this app cannot run without must be in the Android build.

    WHY NO COMPILE CAN CATCH THIS
    -----------------------------
    The Dart compiles fine whether or not a plugin's Android half is in
    the build. The call goes to a method channel with nobody on the other
    end, and you find out on the device:

        PlatformException(channel-error, Unable to establish connection
        on channel: "...FirebaseCoreHostApi.initializeCore")

    That shipped. `.flutter-plugins-dependencies` had been stripped from
    eight Android plugins to four, of which three were native builds -
    jni, jni_flutter and share_plus, exactly the set the broken APK
    turned out to register. Firebase could not start, so the account
    service was null, and all the reader saw was the snackbar "Signing in
    is unavailable just now".

    WHY THIS FILE, AND NOT GeneratedPluginRegistrant.java
    -----------------------------------------------------
    The registrant is generated FROM this file on every build, so it
    repairs itself and cannot serve as the signal. Measured: strip
    firebase_core out of the registrant, run a build, and it comes back.
    This file does not come back. Only `flutter pub get` rewrites it.

    So the names below are the ones the app genuinely cannot work
    without, each with what breaks when it is gone. A plugin added later
    belongs in this list.
    """
    required = {
        "firebase_core": "Firebase will not start at all",
        "firebase_auth": "nobody can sign in",
        "cloud_firestore": "no watchlist and no saved valuations",
        "google_sign_in_android": "the Android sign-in sheet never opens",
        "firebase_app_check": ("nothing is attested, so every request is "
                               "refused once App Check is enforced"),
    }

    deps = APP / ".flutter-plugins-dependencies"
    if not deps.exists():
        return False, f"{deps} is missing. Fix: cd app && flutter pub get"

    plugins = json.loads(deps.read_text(encoding="utf-8"))["plugins"]
    present = {p["name"] for p in plugins.get("android", [])}
    missing = sorted(n for n in required if n not in present)

    if missing:
        lines = [f"{deps.name} lists no Android build for:"]
        lines += [f"  {n} - without it, {required[n]}" for n in missing]
        lines.append("Their method channels will have no Android "
                     "implementation: it compiles, and fails on the device.")
        lines.append("Fix: cd app && flutter pub get   (then rebuild)")
        return False, "\n   ".join(lines)

    return True, (f"all {len(required)} required Android plugins present, "
                  f"of {len(present)}")


CHECKS = [
    # FIRST, and before any build: a build regenerates the registrant from
    # this file, so a check placed after one is reading a repaired copy.
    Check(
        "Android plugins present",
        "a plugin missing from the build, which no compile can catch",
        fn=android_plugins_are_present,
    ),
    Check(
        "python tests",
        "the valuation engine",
        [sys.executable, "-m", "pytest", "-q"],
        ROOT,
    ),
    Check(
        "dart analyze",
        "lints and unresolved names, one import branch at a time",
        ["dart", "analyze", "lib/", "test/"],
        APP,
    ),
    Check(
        "flutter test",
        "the Dart suite",
        ["flutter", "test"],
        APP,
        slow=True,
    ),
    # The two compiles are not redundant. Measured, by planting the same
    # undefined name on each side of the conditional export in
    # excel_delivery.dart: the Android compile caught the io-branch error
    # and was blind to the web-branch one. Neither stands in for the other.
    Check(
        "compile for Android",
        "the non-web branch of every conditional import",
        ["flutter", "build", "bundle", "--target-platform", "android-arm64"],
        APP,
        # Not slow: 11s measured, against 342s for a full APK. --quick
        # keeps it, because the platform gap is what this file is for.
    ),
    Check(
        "compile for web",
        "the web branch, and tree-shaking",
        # --output, NOT the default build/web.
        #
        # A checking build must not replace a build somebody is using.
        # The default output is what tools/serve_web.py serves, and this
        # one carries no --dart-define, so running the checks quietly
        # repointed a local test session at the production API. A check
        # that breaks what it is checking is worse than no check.
        ["flutter", "build", "web", "--release",
         "--output", "build/preflight-web"],
        APP,
        slow=True,
    ),
]


def run(check: Check) -> tuple[bool, float, str]:
    started = time.monotonic()
    if check.fn is not None:
        ok, detail = check.fn()
        return ok, time.monotonic() - started, detail
    proc = subprocess.run(
        check.cmd, cwd=check.cwd, capture_output=True, shell=True)
    took = time.monotonic() - started
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode == 0, took, (out + err)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="skip the slow checks")
    args = parser.parse_args()

    checks = [c for c in CHECKS if not (args.quick and c.slow)]
    failures = []

    for check in checks:
        print(f"... {check.name:24} ({check.why})", flush=True)
        ok, took, output = run(check)
        print(f"{'ok  ' if ok else 'FAIL'} {check.name:24} {took:6.1f}s",
              flush=True)
        if not ok:
            failures.append((check, output))

    print()
    if not failures:
        print(f"All {len(checks)} checks passed.")
        return 0

    for check, output in failures:
        where = " ".join(check.cmd) if check.cmd else check.name
        print("=" * 72)
        print(f"FAILED: {check.name}   ({where})")
        print("=" * 72)
        lines = [ln for ln in output.splitlines() if ln.strip()]
        for line in lines[-40:]:
            print("   " + line)
        print()

    print(f"{len(failures)} of {len(checks)} checks FAILED.")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
