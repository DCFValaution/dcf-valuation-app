/// Pointing the app at the local Firebase emulators, for testing only.
///
/// WHY THIS EXISTS
/// ---------------
/// The parts of the accounts feature worth testing end to end — saving,
/// reopening a save written under an older model, deleting an account and
/// finding nothing left — all sit BEHIND authentication, and testing them
/// against the real project would mean a real Google account, real
/// documents and a real deletion each time.
///
/// The Auth emulator issues genuine ID tokens for accounts it invents, so
/// everything downstream is exercised for real: the same Firestore calls,
/// the same rules, the same deletion order. What it does not exercise is
/// the Google consent handshake itself, which is the one step a person
/// has to do.
///
/// WHY IT CANNOT REACH PRODUCTION
/// ------------------------------
/// Three independent conditions, all of which must hold:
///
///   1. `--dart-define=USE_FIREBASE_EMULATOR=true`, absent from every real
///      build command
///   2. `kDebugMode`, which a release build sets false at compile time
///   3. a host the emulator actually answers on
///
/// Any one of them failing leaves the app talking to the real project with
/// real Google sign-in. The release build strips this code entirely,
/// because `kDebugMode` is a compile-time constant.
library;

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';

/// Whether this build was told to use the emulators, and is allowed to.
const bool useEmulator = kDebugMode &&
    bool.fromEnvironment('USE_FIREBASE_EMULATOR', defaultValue: false);

/// Where the emulators listen. Matches firebase/firebase.json.
const String emulatorHost =
    String.fromEnvironment('FIREBASE_EMULATOR_HOST', defaultValue: 'localhost');
const int authPort = 9099;
const int firestorePort = 8089;

/// Point Auth and Firestore at the emulators. Safe to call always: it
/// returns immediately unless every condition above holds.
Future<void> connectToEmulators() async {
  if (!useEmulator) return;
  await FirebaseAuth.instance.useAuthEmulator(emulatorHost, authPort);
  FirebaseFirestore.instance.useFirestoreEmulator(emulatorHost, firestorePort);
  debugPrint('Firebase: using emulators at $emulatorHost '
      '(auth $authPort, firestore $firestorePort). NOT the real project.');
}

/// A test account, for the emulator only.
///
/// The emulator accepts any address and password and invents the account,
/// so nothing here is a credential: there is no real account to sign in
/// to, and this string is meaningless anywhere else.
const String emulatorTestEmail = 'test-user@example.com';
const String emulatorTestSecret = 'emulator-only-not-a-password';
