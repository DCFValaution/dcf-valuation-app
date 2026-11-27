/// The ORDER of a sign-out, kept apart from Firebase so it can be tested.
///
/// THE BUG THIS FILE EXISTS FOR
/// ----------------------------
/// Sign-out used to await `GoogleSignIn.instance.signOut()` first, inside
/// a `try/catch`:
///
///     try { await GoogleSignIn.instance.signOut(); } catch (_) {}
///     await _auth.signOut();
///
/// A `catch` handles a throw. It does nothing whatsoever about a HANG -
/// and on the web that future never completes. It is the same plugin
/// whose `authenticate()` throws UnimplementedError there: the web
/// implementation expects Google's own rendered button and has no session
/// of its own to end.
///
/// So the second line never ran. The button showed its tap animation and
/// then nothing: no spinner, no message, no console error, and the
/// account still on screen. Nothing was broken in a way anything could
/// report.
///
/// Two rules come out of it, and they generalise past this one function:
///
///   * the step that MATTERS goes first. Firebase Auth is what
///     `authStateChanges` reports and what the Firestore rules key on, so
///     the moment it returns, the person IS signed out - whatever happens
///     next;
///   * a nicety gets a TIMEOUT. Anything after the thing that matters is
///     allowed to fail, and is not allowed to wait forever.
///
/// Separating it from Firebase is not ceremony: with the two steps passed
/// in, a test can hang one and prove the other still ran, which is the
/// single property the shipped version got wrong.
library;

import 'dart:async';

import 'release_log.dart';

/// Sign out of [firebase], then give [googleSession] a bounded go.
///
/// Pass `null` for [googleSession] where there is no Google session to
/// end - the web, where the call never returns. Its failure, including a
/// timeout, is logged and swallowed: by then the sign-out has happened.
Future<void> signOutInOrder({
  required Future<void> Function() firebase,
  Future<void> Function()? googleSession,
  Duration googleTimeout = const Duration(seconds: 3),
}) async {
  logStep(
      'sign-out',
      googleSession == null
          ? 'start (Firebase only)'
          : 'start (Firebase, then Google)');

  await firebase();
  logStep('sign-out', 'Firebase done');

  if (googleSession != null) {
    try {
      await googleSession().timeout(googleTimeout);
      logStep('sign-out', 'Google done');
    } catch (e) {
      logFailure('sign-out Google session', e);
    }
  }
  logStep('sign-out', 'complete');
}

/// Run something whose failure does not matter, and whose hanging must
/// not matter either.
///
/// For the trailing steps of a deletion: the account is already gone, so
/// clearing a local Google session is worth attempting and not worth
/// waiting on. Same hang, same place, same fix.
Future<void> bestEffort(
  String what,
  Future<void> Function() step, {
  Duration timeout = const Duration(seconds: 3),
}) async {
  try {
    await step().timeout(timeout);
  } catch (e) {
    logFailure(what, e);
  }
}
