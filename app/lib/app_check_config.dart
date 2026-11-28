import 'package:flutter/foundation.dart' show kIsWeb;

/// The reCAPTCHA Enterprise site key, for App Check on the web.
///
/// A public key - it ships in the page either way - but environment
/// specific, so it is a define rather than a constant. Empty means "do not
/// activate the web provider", which is the right behaviour for a local
/// build: activating with no key throws, and a developer build that
/// refuses to start because of an anti-abuse measure helps nobody.
///
///     flutter build web --dart-define=RECAPTCHA_SITE_KEY=6Lxxxxx
const String kRecaptchaSiteKey =
    String.fromEnvironment('RECAPTCHA_SITE_KEY');

/// Whether this build has what App Check needs, and so whether
/// `FirebaseAppCheck.instance.activate()` was called at startup.
///
/// Two callers depend on the same answer and must not be able to disagree
/// about it, which is why the condition lives here rather than being
/// written out twice:
///
///   * startup decides whether to call `activate()` at all;
///   * the token fetch decides whether to call `getToken()`.
///
/// Calling `getToken()` without `activate()` does not return null. It
/// throws - "Before using other Firebase App Check APIs,
/// FirebaseAppCheck.instance.activate() must be called first" - on every
/// request, which is noise describing a decision this build already made
/// on purpose. Asking for a token the build cannot have is not best
/// effort; it is a question with a known answer.
///
/// False only on a web build with no site key. Android always configures a
/// provider, and an Android `activate()` that fails anyway is a genuine
/// failure the token fetch should still report.
/// `== ''` rather than `.isEmpty`: a property access on a String is not
/// allowed in a constant expression, and this has to be a compile-time
/// constant so the web build can tree-shake the branch it never takes.
const bool kAppCheckConfigured = !(kIsWeb && kRecaptchaSiteKey == '');
