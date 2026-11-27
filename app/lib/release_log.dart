/// Logging that survives a release build.
///
/// `debugPrint` is stripped from release, and a release-only failure is
/// exactly the one nobody can reproduce: the undeployed Firestore rules
/// only ever showed up in a real deployment, and the web sign-out hang
/// only ever happened in a browser. `print` inside a release guard is the
/// crude answer, and the crude answer is the one that works here.
library;

import 'package:flutter/foundation.dart';

/// Something went wrong, named with its type.
///
/// The type matters as much as the message: `FirebaseException` and
/// `FirebaseAuthException` read almost alike in a message and are caught
/// by different handlers, which is how a failed Firestore write came to
/// fail a sign-in that had succeeded.
void logFailure(String where, Object error, [StackTrace? stack]) {
  _write('[$where] ${error.runtimeType}: $error');
  if (!kReleaseMode && stack != null) debugPrintStack(stackTrace: stack);
}

/// A step in a sequence that can stop halfway.
///
/// Sign-out is three awaits in a row and used to hang on the first with
/// nothing on screen and nothing in the console. One line per step turns
/// "it does nothing" into "it stopped after this one".
void logStep(String where, String what) => _write('[$where] $what');

void _write(String line) {
  if (kReleaseMode) {
    // ignore: avoid_print
    print(line);
  } else {
    debugPrint(line);
  }
}
