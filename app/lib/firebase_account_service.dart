/// [AccountService] on Firebase Auth and Firestore.
///
/// The screens never see this class — they take the interface, which is
/// what keeps every one of their states reachable in a test without a
/// network, a provider or a real account to destroy.
///
/// THE ORDER OF A DELETION IS LOAD-BEARING
/// ---------------------------------------
/// Documents first, auth user last. Firestore rules allow a write only
/// when `request.auth.uid` matches the path, so deleting the auth user
/// first would leave documents that nobody — not the owner, not us, not
/// a later retry — can reach, read or delete. They would simply sit
/// there, against a promise that they are gone.
///
/// That ordering is the single most important thing in this file, which is
/// why the fake records its calls and a test asserts the sequence.
library;

import 'dart:async';

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_app_check/firebase_app_check.dart';
import 'package:firebase_auth/firebase_auth.dart' as fb;
import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:google_sign_in/google_sign_in.dart';

import 'account_service.dart';
import 'firebase_emulator.dart';
import 'release_log.dart';
import 'sign_out_sequence.dart';

/// Where a user's own data lives. One place, so the deletion and the rules
/// cannot drift apart.
String userPath(String uid) => 'users/$uid';

/// Where the free-valuation counter lives.
///
/// Written by the BACKEND, as the caller - see usage.py - and read and
/// deleted by the app. It is here rather than under `users/{uid}` because
/// an anonymous installation has a counter and no user record at all.
String usagePath(String uid) => 'usage/$uid';

class FirebaseAccountService implements AccountService {
  FirebaseAccountService({
    fb.FirebaseAuth? auth,
    FirebaseFirestore? firestore,
  })  : _auth = auth ?? fb.FirebaseAuth.instance,
        _db = firestore ?? FirebaseFirestore.instance {
    _subscription = _auth.authStateChanges().listen((user) {
      _current = _toAccount(user);
      _controller.add(_current);

      // NOBODY SIGNED IN IS NOT A STATE THIS APP STAYS IN.
      //
      // Signing out, or deleting the account, leaves Firebase with no
      // user - and with no user there is no verified subject id, so the
      // free allowance would have nothing to count against but the IP
      // address, which is shared by a household, an office or a campus.
      // So the app signs itself straight back in anonymously.
      //
      // This cannot loop: a successful anonymous sign-in makes `user`
      // non-null, and a failed one produces no auth change at all.
      if (user == null) {
        unawaited(_anonymouslyQuietly());
      }
    });
    _current = _toAccount(_auth.currentUser);
  }

  final fb.FirebaseAuth _auth;
  final FirebaseFirestore _db;
  final _controller = StreamController<Account?>.broadcast();
  StreamSubscription<fb.User?>? _subscription;
  Account? _current;

  static Account? _toAccount(fb.User? user) => user == null
      ? null
      : Account(
          uid: user.uid,
          email: user.email,
          isAnonymous: user.isAnonymous,
        );

  @override
  Account? get current => _current;

  @override
  Stream<Account?> get changes => _controller.stream;

  @override
  Future<void> ensureSignedIn() async {
    if (_auth.currentUser != null) return;
    await _anonymouslyQuietly();
  }

  /// Anonymous sign-in, which never fails loudly.
  ///
  /// A reader whose anonymous sign-in did not work still gets the whole
  /// app. They arrive at the API unattributed, where the per-address cap
  /// governs them instead - a worse deal for them, and still a working
  /// app, which is the right way round.
  Future<void> _anonymouslyQuietly() async {
    try {
      await _auth.signInAnonymously();
      logStep('anonymous-sign-in', 'done');
    } catch (e, stack) {
      logFailure('anonymous sign-in', e, stack);
    }
  }

  @override
  Future<AuthTokens?> tokens() async {
    final user = _auth.currentUser;
    if (user == null) return null;

    String? id;
    try {
      id = await user.getIdToken();
    } catch (e) {
      logFailure('id token', e);
    }

    // Best effort, and deliberately separate. App Check can be
    // unavailable for reasons that have nothing to do with the reader -
    // an unregistered debug build, a Play Integrity hiccup - and losing
    // the attestation must not also lose the identity.
    String? attestation;
    try {
      attestation = await FirebaseAppCheck.instance.getToken();
    } catch (e) {
      logFailure('app check token', e);
    }

    if (id == null && attestation == null) return null;
    return AuthTokens(idToken: id, appCheckToken: attestation);
  }

  @override
  Future<void> signIn() async {
    if (useEmulator) {
      // The Auth emulator invents the account, so there is no Google
      // handshake to make and no credential involved. Everything after
      // this line - the token, the rules, the documents - is the real
      // path. See firebase_emulator.dart for why this cannot ship.
      await _signInToEmulator();
      return;
    }
    if (kIsWeb) {
      // google_sign_in does not do this on the web. Its web implementation
      // returns `supportsAuthenticate() => false` and `authenticate()`
      // throws UnimplementedError, telling you to render Google's own
      // button instead. Calling it anyway is what shipped first: the
      // UnimplementedError escaped the handlers below, the sheet never
      // cleared its busy flag, and "Signing in…" span forever with no
      // Google window and nothing on screen to say why.
      await _signInWithPopup();
      return;
    }
    try {
      // google_sign_in 7 requires initialize() before any authenticate(),
      // on every platform that supports it - which is Android here.
      await GoogleSignIn.instance.initialize();
      final account = await GoogleSignIn.instance.authenticate();
      final tokens = account.authentication;
      final credential =
          fb.GoogleAuthProvider.credential(idToken: tokens.idToken);
      await _signInOrLink(credential);
      // Same reasoning as the web path: a Firestore failure must not
      // undo a sign-in that succeeded.
      await _touchUserRecordQuietly();
    } on GoogleSignInException catch (e) {
      // Dismissing the sheet is not a failure. It reaches the caller as
      // `cancelled`, which the screens treat as "they changed their mind"
      // rather than something to apologise for.
      if (e.code == GoogleSignInExceptionCode.canceled) {
        throw const AccountError(cancelled);
      }
      throw AccountError(_plain(e.description ?? e.code.name));
    } on fb.FirebaseAuthException catch (e) {
      throw AccountError(_plain(e.message ?? e.code));
    }
  }

  /// Link if we are on the anonymous account, otherwise sign in.
  ///
  /// `linkWithCredential` KEEPS THE SAME uid. That is the whole reason
  /// this is not simply `signInWithCredential`: the free valuations
  /// already counted are keyed on that uid, and so is everything else the
  /// installation has. Linking carries them across; signing in would
  /// strand them under an identity nobody can present again.
  Future<void> _signInOrLink(fb.AuthCredential credential) async {
    final user = _auth.currentUser;
    if (user == null || !user.isAnonymous) {
      await _auth.signInWithCredential(credential);
      return;
    }

    final stranded = user.uid;
    try {
      await user.linkWithCredential(credential);
      logStep('sign-in', 'linked; the uid and the valuation count are kept');
    } on fb.FirebaseAuthException catch (e) {
      if (!_alreadyHasAnAccount(e.code)) rethrow;
      await _switchToTheExistingAccount(stranded,
          () => _auth.signInWithCredential(credential));
    }
  }

  /// They already had this Google account, so there is nothing to link to.
  ///
  /// They sign in to the account they had, and the anonymous identity is
  /// discarded. Its counter goes with it - DELETED FIRST, while we are
  /// still signed in as that uid, because the moment the identity changes
  /// the rules will not let anybody reach those documents again, and they
  /// would sit there against a privacy policy that says they do not.
  ///
  /// Nothing else needs migrating: an anonymous installation cannot save
  /// a valuation or keep a watchlist, which the Firestore rules enforce
  /// rather than merely assume.
  Future<void> _switchToTheExistingAccount(
      String stranded, Future<void> Function() signIn) async {
    logStep('sign-in',
        'that Google account already exists; discarding the anonymous one');
    await _deleteUsageQuietly(stranded);
    await signIn();
  }

  static bool _alreadyHasAnAccount(String code) =>
      code == 'credential-already-in-use' ||
      code == 'email-already-in-use' ||
      code == 'account-exists-with-different-credential' ||
      code == 'provider-already-linked';

  /// Google sign-in on the web, through Firebase Auth's own pop-up.
  ///
  /// Firebase opens and owns the window, so none of google_sign_in's web
  /// restrictions apply. A pop-up blocker is the one failure worth
  /// handling specially: falling back to a redirect costs the page but
  /// always works, and the alternative is telling somebody to go and find
  /// a setting they have never heard of.
  ///
  /// `signInWithRedirect` does not return - the page navigates away and
  /// comes back signed in - so nothing after it runs.
  Future<void> _signInWithPopup() async {
    final provider = fb.GoogleAuthProvider();
    // Always offer the chooser.
    //
    // On the web we do not end the Google session on sign-out (that call
    // never returns there), so without this the next sign-in would pick
    // the last account silently - which on a shared computer signs
    // somebody into an account that is not theirs. Asking Google for the
    // chooser costs a click and cannot hang.
    provider.setCustomParameters({'prompt': 'select_account'});
    try {
      await _popupSignInOrLink(provider);
      // NOT awaited into the same try as the sign-in.
      //
      // `_touchUserRecord` writes to Firestore, and a Firestore failure
      // threw a FirebaseException - not a FirebaseAuthException - which
      // escaped the handler below and failed the whole sign-in. The
      // reader saw "Signing in did not finish" while Firebase Auth had
      // in fact signed them in: reloading the page showed them signed in,
      // which is as confusing as an app can be.
      //
      // That is exactly what the undeployed rules did. But the lesson
      // outlives the cause: being signed in and having a profile row are
      // different things, and the first does not depend on the second.
      await _touchUserRecordQuietly();
    } on fb.FirebaseAuthException catch (e) {
      // The CODE, always, before anything else happens to it.
      //
      // Without this the only evidence of a failed sign-in was the
      // sentence shown to the reader, which is deliberately vague - so a
      // real failure was indistinguishable from any other, and the first
      // one cost a round trip to find out what it even was.
      debugPrint('Firebase sign-in failed: code=${e.code} '
          'message=${e.message}');
      switch (e.code) {
        // They shut the window, or a second attempt superseded the first.
        // Neither is a failure, and neither deserves a red message.
        case 'popup-closed-by-user':
        case 'cancelled-popup-request':
        case 'user-cancelled':
          throw const AccountError(cancelled);

        // The pop-up could not be opened, or it opened and could not
        // report back. The second is the Cross-Origin-Opener-Policy case:
        // Google's own sign-in pages send COOP: same-origin, which severs
        // the opener link, so the app cannot see the window close and
        // Firebase gives up on it. A redirect has no window to watch and
        // no opener to lose, so it is immune to all of this - it costs
        // the page, which is why it is the fallback rather than the
        // default.
        case 'popup-blocked':
        case 'web-storage-unsupported':
        case 'internal-error':
        case 'timeout':
          debugPrint('Firebase sign-in: falling back to redirect.');
          await _auth.signInWithRedirect(provider);
          return;

        case 'unauthorized-domain':
          // The one failure a redirect will not fix, and the one with a
          // precise remedy - so it says the remedy rather than "try
          // again", which would be advice to repeat a thing that cannot
          // work.
          throw const AccountError(
            'This address is not an authorised domain for sign-in. Add it '
            'in Firebase console → Authentication → Settings → Authorised '
            'domains.',
          );

        default:
          throw AccountError('${_plain(e.message ?? e.code)} (${e.code})');
      }
    }
  }

  /// The web half of [_signInOrLink].
  ///
  /// Anything that is not "you already have this account" is rethrown
  /// untouched, so the pop-up-blocked and COOP handling above still sees
  /// the codes it exists for.
  Future<void> _popupSignInOrLink(fb.GoogleAuthProvider provider) async {
    final user = _auth.currentUser;
    if (user == null || !user.isAnonymous) {
      await _auth.signInWithPopup(provider);
      return;
    }

    final stranded = user.uid;
    try {
      await user.linkWithPopup(provider);
      logStep('sign-in', 'linked; the uid and the valuation count are kept');
    } on fb.FirebaseAuthException catch (e) {
      if (!_alreadyHasAnAccount(e.code)) rethrow;
      await _switchToTheExistingAccount(
          stranded, () => _auth.signInWithPopup(provider));
    }
  }

  /// Sign in to the local Auth emulator, creating the account if needed.
  Future<void> _signInToEmulator() async {
    try {
      await _auth.signInWithEmailAndPassword(
          email: emulatorTestEmail, password: emulatorTestSecret);
    } on fb.FirebaseAuthException {
      await _auth.createUserWithEmailAndPassword(
          email: emulatorTestEmail, password: emulatorTestSecret);
    }
    await _touchUserRecordQuietly();
  }

  /// Write the user record, and never fail a sign-in over it.
  ///
  /// Signing in and having a profile row are different things. If this
  /// cannot write — denied rules, a dropped connection, a quota — the
  /// person IS signed in, their watchlist and saves still work, and the
  /// row will be written on their next sign-in. Turning that into a
  /// failed sign-in is strictly worse than the missing row.
  ///
  /// It still says so in the console, because a permission denial here
  /// means the rules are not what anybody thinks they are.
  Future<void> _touchUserRecordQuietly() async {
    try {
      await _touchUserRecord();
    } catch (e) {
      debugPrint('Signed in, but the user record could not be written: '
          '${e.runtimeType}: $e');
    }
  }

  /// Create or refresh the user record.
  ///
  /// Exactly the three fields the rules allow: anything else is refused
  /// there, which is how "we do not keep your name or your photo" stays
  /// true rather than merely intended.
  Future<void> _touchUserRecord() async {
    final user = _auth.currentUser;
    if (user == null) return;
    final doc = _db.doc(userPath(user.uid));
    final existing = await doc.get();
    await doc.set({
      'email': user.email ?? '',
      if (!existing.exists) 'created_at': FieldValue.serverTimestamp(),
      if (existing.exists) 'created_at': existing.data()?['created_at'],
      'last_seen_at': FieldValue.serverTimestamp(),
    });
  }

  /// The order is load-bearing and the timeout is load-bearing. Both
  /// live in `sign_out_sequence.dart`, with the bug they exist for.
  @override
  Future<void> signOut() => signOutInOrder(
        firebase: _auth.signOut,
        // Nothing to end on the web, and the call never returns there.
        // The account chooser that this used to buy us comes from
        // `prompt: select_account` on the provider instead.
        googleSession: kIsWeb ? null : GoogleSignIn.instance.signOut,
      );

  @override
  Future<void> deleteAccount() async {
    final user = _auth.currentUser;
    if (user == null) {
      throw const AccountError('You are not signed in.');
    }
    // How far it got, because the message depends on it.
    //
    // Documents first means a failure at `user.delete()` - and
    // `requires-recent-login` is the ordinary one - leaves the data
    // ALREADY GONE and the account still standing. Reporting "nothing has
    // been removed" there is a false statement about an irreversible
    // action, which is the one thing this path must never make.
    var dataGone = false;
    try {
      // Documents first. See the note at the top of this file.
      await _deleteUserData(user.uid);
      dataGone = true;
      logStep('delete-account', 'documents deleted');
      await user.delete();
      logStep('delete-account', 'auth user deleted');
      if (!kIsWeb) {
        // The SAME hang, in the same plugin, one step later - and worse
        // here, because it sits after the deletion: the account would be
        // genuinely gone while the screen waited forever to say so. The
        // account is gone either way, so this is best-effort by
        // definition.
        await bestEffort(
            'delete-account Google session', GoogleSignIn.instance.signOut);
      }
      logStep('delete-account', 'complete');
    } on fb.FirebaseAuthException catch (e) {
      logFailure('delete-account', e);
      if (e.code == 'requires-recent-login') {
        throw AccountError(dataGone
            ? 'Your watchlist and saved valuations have been deleted. For '
                'your security, the account itself needs a fresh sign-in: '
                'please sign in again and delete it.'
            : 'For your security, please sign in again and then delete the '
                'account. Nothing has been removed.');
      }
      throw AccountError(_withProgress(_plain(e.message ?? e.code), dataGone));
    } on FirebaseException catch (e) {
      logFailure('delete-account', e);
      throw AccountError(_withProgress(_plain(e.message ?? e.code), dataGone));
    }
  }

  /// The message, plus the truth about what is already gone.
  ///
  /// The screens used to append "Nothing has been removed." to every
  /// failure, which they cannot know and which is wrong for every failure
  /// after the first step. Said here instead, where the answer is known.
  static String _withProgress(String message, bool dataGone) => dataGone
      ? '$message Your watchlist and saved valuations have been deleted; '
          'the account itself has not.'
      : '$message Nothing has been removed.';

  /// Every document under this user, then the user record itself.
  ///
  /// `saves` is queried because its document ids are generated. `lists`
  /// is NOT: the rules name `lists/watchlist` and deny `lists/{other}`,
  /// and a collection query has to satisfy the rules for every document
  /// it could return — so querying `lists` is refused even though reading
  /// the one document in it is allowed.
  ///
  /// Found by running the deletion against the emulator, where it left
  /// the saves gone, the watchlist behind and the account intact. That it
  /// was survivable is the documents-before-auth-user order doing its
  /// job: with the auth user deleted first, the watchlist would have been
  /// stranded under a uid nobody can ever present again.
  Future<void> _deleteUserData(String uid) async {
    final root = _db.doc(userPath(uid));

    final saves = await root.collection('saves').get();
    for (final d in saves.docs) {
      await d.reference.delete();
    }

    // Named, not queried. Any list added later needs a line here and a
    // rule of its own, which is the point of denying `lists/{other}`.
    await root.collection('lists').doc('watchlist').delete();

    // The valuation markers, one per company valued per day.
    //
    // A document's subcollections do NOT go when the document does -
    // Firestore treats them as independent - so deleting `usage/{uid}`
    // without this would leave markers stranded under a uid nobody can
    // present again. Exactly the data the deletion page promises is gone.
    await _deleteMarkers(uid);

    // THE USER RECORD AND THE COUNTER GO IN ONE COMMIT.
    //
    // The rule is `allow delete: if owner(uid) && !existsAfter(users/
    // $(uid))`: the counter may only be deleted by a write that leaves no
    // user record behind. That is what stops a signed-in reader deleting
    // their counter to reset their own free allowance, and it means the
    // only legitimate deletion of a counter is this one - the account
    // going away entirely. Split these into two writes and the second
    // fails, permanently.
    final batch = _db.batch();
    batch.delete(root);
    batch.delete(_db.doc(usagePath(uid)));
    await batch.commit();
  }

  /// Every "already counted this company today" marker, in pages.
  ///
  /// Paged because a reader who has been here a while has one per company
  /// per day, and a single unbounded `get()` on a collection that large
  /// is a document read for each of them in one request.
  Future<void> _deleteMarkers(String uid) async {
    const pageSize = 300;
    while (true) {
      final page = await _db
          .collection('${usagePath(uid)}/valued')
          .limit(pageSize)
          .get();
      if (page.docs.isEmpty) return;
      for (final d in page.docs) {
        await d.reference.delete();
      }
      if (page.docs.length < pageSize) return;
    }
  }

  /// Remove an anonymous installation's counter, and never fail over it.
  ///
  /// Used when the reader turns out to have a Google account already, so
  /// the anonymous uid is about to be abandoned. Best effort because the
  /// sign-in they asked for matters more than the tidy-up - but it runs
  /// BEFORE the identity changes, because afterwards it cannot run at all.
  Future<void> _deleteUsageQuietly(String uid) async {
    try {
      await _deleteMarkers(uid);
      await _db.doc(usagePath(uid)).delete();
      logStep('discard-anonymous', 'counter and markers deleted');
    } catch (e, stack) {
      logFailure('discard anonymous usage', e, stack);
    }
  }

  @override
  Future<Map<String, Object?>> exportData() async {
    final user = _auth.currentUser;
    if (user == null) {
      throw const AccountError('You are not signed in.');
    }
    final root = _db.doc(userPath(user.uid));
    final record = await root.get();
    final watchlist = await root.collection('lists').doc('watchlist').get();
    final saves = await root.collection('saves').get();

    // The counter is included because we hold it, and this file is
    // supposed to be everything. The account screen lists it too, under
    // "What we hold" - a thing we store and do not show would make both
    // of those a lie.
    Map<String, Object?>? counter;
    try {
      counter = (await _db.doc(usagePath(user.uid)).get()).data();
    } catch (e) {
      logFailure('export usage', e);
    }

    return {
      'account': record.data(),
      'watchlist': watchlist.data(),
      'saved_valuations': {
        for (final d in saves.docs) d.id: d.data(),
      },
      'valuation_counter': counter,
      'exported_at': DateTime.now().toUtc().toIso8601String(),
      'note': 'Everything Intrinsic holds about you. Nothing else exists.',
    };
  }

  @override
  Future<int?> valuationsCounted() async {
    final user = _auth.currentUser;
    if (user == null) return null;
    try {
      final doc = await _db.doc(usagePath(user.uid)).get();
      final value = doc.data()?['lifetime'];
      return value is int ? value : null;
    } catch (e) {
      // Not worth failing the screen over. The row simply says it could
      // not be read, which is more honest than showing a zero that would
      // read as "we hold nothing".
      logFailure('valuations counted', e);
      return null;
    }
  }

  /// A sentence for a person, not a provider error code.
  ///
  /// "PERMISSION_DENIED" tells a reader nothing about whether their data
  /// is still there, which on the deletion page is the only question.
  static String _plain(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) return 'Something went wrong. Please try again.';
    return trimmed;
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _controller.close();
  }
}
