/// Which Firebase project a build talks to.
///
/// The emulator switch is the one piece of this feature that could, if it
/// were wrong, make a test look like it passed against the real project
/// when it did not — or worse, point a shipped build at a local emulator
/// that does not exist, so nobody can sign in at all.
///
/// Three independent conditions guard it, and this file pins two of them.
/// The third, `kDebugMode`, is a compile-time constant that a release
/// build sets false, which was verified by building the release web
/// bundle and finding no emulator code in it at all — not the strings,
/// not `useAuthEmulator`, nothing. The tree-shaker removes the branch.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/firebase_emulator.dart';
import 'package:dcf_app/firebase_options.dart';

void main() {
  group('the emulator switch', () {
    test('is off unless the build was told otherwise', () {
      // `flutter test` runs in debug mode, so kDebugMode is true here and
      // this is a real check of the OTHER condition: without
      // --dart-define=USE_FIREBASE_EMULATOR=true, the switch is off.
      //
      // If this ever fails, a plain `flutter run` is talking to an
      // emulator that is probably not running, and sign-in is broken for
      // everyone with no error that says why.
      expect(useEmulator, isFalse);
    });

    test('names a host and ports rather than scattering them', () {
      // So that "which emulator" is one answer in one place, and the
      // Android case (10.0.2.2) is a dart-define rather than an edit.
      expect(emulatorHost, isNotEmpty);
      expect(authPort, 9099);
      expect(firestorePort, 8089);
    });
  });

  group('the real project', () {
    test('is the one the console was set up for', () {
      // A typo here points every signed-in user at a project that does
      // not exist, and the only symptom is sign-in failing.
      expect(DefaultFirebaseOptions.android.projectId, 'intrinsic-e3abf');
      expect(DefaultFirebaseOptions.web.projectId, 'intrinsic-e3abf');
    });

    test('is the same project on both platforms', () {
      // Two projects would give a reader a different watchlist on their
      // phone and their laptop, which is the one thing signing in is
      // supposed to prevent.
      expect(
        DefaultFirebaseOptions.android.projectId,
        DefaultFirebaseOptions.web.projectId,
      );
      expect(
        DefaultFirebaseOptions.android.messagingSenderId,
        DefaultFirebaseOptions.web.messagingSenderId,
      );
    });

    test('carries no Analytics measurement id', () {
      // The data-safety form says no tracking. The generator writes
      // `measurementId` into the web options and that switches Google
      // Analytics on, so its absence is the thing that makes the form
      // answer true rather than merely intended.
      expect(DefaultFirebaseOptions.web.measurementId, isNull);
      expect(DefaultFirebaseOptions.android.measurementId, isNull);
    });

    test('states where the data is held, matching the privacy policy', () {
      // Chosen when the project was created and not changeable
      // afterwards, so the policy names it rather than saying "our
      // servers".
      expect(kFirestoreRegion, 'asia-southeast1');
    });
  });
}
