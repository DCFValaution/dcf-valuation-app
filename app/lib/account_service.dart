/// Signing in, signing out, and going away entirely.
///
/// An interface rather than a Firebase call, for three reasons that are
/// all the same reason: the screens have to be testable without a network,
/// the deletion page has to be reachable in every state without arranging
/// an account first, and the one piece of this feature that must be
/// provably correct - that deleting removes everything - should be
/// checkable without deleting anything real.
///
/// The Firebase implementation lands on top of this and changes none of
/// the screens.
library;

import 'dart:async';

/// Who is signed in.
///
/// Two fields, matching what the backend verifies and what the rules key
/// on. Deliberately not a copy of the Google profile: no display name, no
/// photo, no locale. The privacy policy says we do not keep those, and a
/// field that does not exist cannot quietly start being stored.
class Account {
  const Account({
    required this.uid,
    this.email,
    this.isAnonymous = false,
  });

  /// The Firebase subject. Everything saved lives under this, and it is
  /// what the backend reads out of the ID token.
  final String uid;

  /// Shown on the account screen so a person with two Google accounts can
  /// see which one they are about to delete.
  final String? email;

  /// Whether this is the anonymous account every installation gets.
  ///
  /// SIGNED IN NO LONGER MEANS WHAT IT USED TO. The app signs every
  /// installation in anonymously at startup, because that is what lets
  /// the free allowance be counted per installation on the server rather
  /// than per IP address. So `current != null` is now true almost always,
  /// and the question the screens actually want is [AccountService.signedIn].
  final bool isAnonymous;

  @override
  bool operator ==(Object other) =>
      other is Account &&
      other.uid == uid &&
      other.email == email &&
      other.isAnonymous == isAnonymous;

  @override
  int get hashCode => Object.hash(uid, email, isAnonymous);
}

/// What a request needs to prove who and what it comes from.
///
/// Two tokens with two different jobs: the ID token says WHICH PERSON (or
/// which anonymous installation), and the App Check token says THIS IS
/// REALLY THE APP. The backend verifies both, and forwards both to
/// Firestore when it writes the counter, which is why enforcing App Check
/// on Firestore does not lock the backend out.
class AuthTokens {
  const AuthTokens({this.idToken, this.appCheckToken});

  final String? idToken;
  final String? appCheckToken;

  /// The headers to send. Empty entries are omitted rather than sent
  /// blank, because an empty Authorization header is a malformed one.
  Map<String, String> get headers => {
        if (idToken != null && idToken!.isNotEmpty)
          'Authorization': 'Bearer $idToken',
        if (appCheckToken != null && appCheckToken!.isNotEmpty)
          'X-Firebase-AppCheck': appCheckToken!,
      };
}

/// Raised when a sign-in or a deletion could not be completed.
///
/// Carries a sentence meant for a person, not a provider error code: the
/// deletion page prints it, and "PERMISSION_DENIED" tells a reader nothing
/// about whether their data is still there.
class AccountError implements Exception {
  const AccountError(this.message);

  final String message;

  @override
  String toString() => message;
}

abstract class AccountService {
  /// Who is signed in now, or null. Synchronous, because the screens need
  /// an answer on their first build rather than a frame of flicker.
  Account? get current;

  /// Changes to [current], including the first value.
  Stream<Account?> get changes;

  /// Make sure there is at least an anonymous account.
  ///
  /// Called once at startup. It is what gives a signed-out reader a
  /// verified subject id, which is what lets the free allowance be
  /// counted per installation on the server instead of per IP address -
  /// and per IP address was never acceptable, because an address is
  /// shared by a household, an office or a whole campus.
  ///
  /// Never throws. A reader whose anonymous sign-in failed still gets the
  /// app; the server falls back to its per-address cap for them.
  Future<void> ensureSignedIn();

  /// The tokens for an API call, or null if there are none to be had.
  ///
  /// Never throws, for the same reason: a valuation must not fail because
  /// a token could not be minted. An unattributed request is governed by
  /// the per-address cap instead, which is a worse deal for the reader
  /// but is still a working app.
  Future<AuthTokens?> tokens();

  /// Sign in with Google. Throws [AccountError] if it does not happen,
  /// including when the person simply dismisses the sheet - which is not
  /// an error to report, so callers check for [cancelled].
  ///
  /// LINKS rather than replaces, when the current account is the
  /// anonymous one: `linkWithCredential` keeps the same uid, so the free
  /// valuations already counted, and anything else keyed on that uid,
  /// come with the reader instead of being stranded.
  ///
  /// The exception is a Google account that already exists. There is no
  /// linking to be done then - the reader simply signs in to the account
  /// they already had - and the anonymous uid is discarded along with its
  /// counter. Nothing is lost, because an anonymous installation cannot
  /// save anything in the first place.
  Future<void> signIn();

  /// Forget the session on this device. Removes nothing from the server,
  /// and the account screen says so.
  Future<void> signOut();

  /// Remove the account and everything stored under it.
  ///
  /// Everything: the user record, the watchlist and every saved
  /// valuation. Not a flag, not a queue, not "within 30 days" as far as
  /// the app is concerned - the documents are gone when this returns, and
  /// the 30 days in the privacy policy is about backups.
  Future<void> deleteAccount();

  /// Everything held about the signed-in user, for Account → Download.
  Future<Map<String, Object?>> exportData();

  /// How many valuations have been counted against this account, or null
  /// if it cannot be read.
  ///
  /// Shown on the account screen under "What we hold", because we DO hold
  /// it. A number we store and do not show would make that list a lie,
  /// and that list is the point of the screen.
  Future<int?> valuationsCounted();

  void dispose();
}

/// Whether there is a REAL account, as opposed to the anonymous one every
/// installation has.
///
/// This is what the screens mean by "signed in". Checking `current != null`
/// would now be true for a reader who has never signed in to anything, and
/// would show them an account screen about an account they do not have.
///
/// An extension rather than a member, so that neither implementation has to
/// carry a copy of one line of logic that must be the same in both.
extension AccountServiceState on AccountService {
  bool get signedIn {
    final who = current;
    return who != null && !who.isAnonymous;
  }

  /// The anonymous account, if that is what we are on. Null when signed
  /// in properly, and null when there is nothing at all.
  Account? get anonymousAccount {
    final who = current;
    return (who != null && who.isAnonymous) ? who : null;
  }
}

/// Returned by [AccountService.signIn] via [AccountError] when the person
/// dismissed the provider's sheet. Not a failure, and not worth a red box.
const String cancelled = 'cancelled';

/// An account service with no provider behind it.
///
/// Used by tests, and by a development build that has no Firebase config -
/// which is every build until the console work is done, so this is what
/// keeps the screens buildable in the meantime.
class FakeAccountService implements AccountService {
  FakeAccountService({Account? signedIn}) : _current = signedIn {
    _controller.add(_current);
  }

  final _controller = StreamController<Account?>.broadcast();
  Account? _current;

  /// Set by a test to make the next call fail with an AccountError.
  String? failNextSignIn;

  /// Set by a test to make the next sign-in throw something that is NOT
  /// an AccountError.
  ///
  /// This is the shape of the bug that shipped: google_sign_in's web
  /// implementation throws UnimplementedError from authenticate(), a
  /// handler that caught only AccountError let it escape, and the sheet
  /// span forever. A fake that can only fail in the expected way cannot
  /// catch that.
  Object? throwNextSignIn;
  String? failNextDelete;

  /// Set by a test to hold [deleteAccount] open, so the in-flight state is
  /// observable. Without it the fake completes within the same frame and
  /// the state a user would actually see for a second or two is
  /// unreachable - which is how a double-tap guard goes untested.
  Completer<void>? holdDelete;

  /// Set by a test to hold [signOut] open, which is what the web
  /// actually did: GoogleSignIn.instance.signOut() never completed, so
  /// the screen was left waiting on a future that would not resolve.
  Completer<void>? holdSignOut;

  /// Set by a test to make the next sign-out throw.
  Object? throwNextSignOut;

  /// Set by a test to clear the account and THEN throw, without emitting
  /// on [changes]. That is a real shape: the sign-out succeeded and
  /// something after it failed, and the screen must not report failure
  /// over an account that is already gone. Not emitting is deliberate -
  /// it isolates the handler's own check from the auth listener, which
  /// would otherwise do the job and hide a regression in it.
  bool signOutThrowsAfterClearing = false;

  /// What the fake stored, so a test can prove deletion emptied it.
  final Map<String, Object?> stored = {};

  /// Every call made, in order, so a test can prove the ORDER of a
  /// deletion: documents first, account last. A deletion that removed the
  /// account first and then failed would leave orphaned data nobody can
  /// reach, read or delete.
  final List<String> calls = [];

  @override
  Account? get current => _current;

  @override
  Stream<Account?> get changes => _controller.stream;

  /// Set by a test to make the next sign-in hit a Google account that
  /// already exists, so there is nothing to link to and the anonymous uid
  /// is discarded instead.
  bool nextSignInFindsAnExistingAccount = false;

  /// The anonymous uid this fake started with, so a test can prove
  /// linking KEPT it and the already-exists path did not.
  String? anonymousUid;

  @override
  Future<void> ensureSignedIn() async {
    calls.add('ensureSignedIn');
    if (_current != null) return;
    anonymousUid = 'anon-install-1';
    _current = Account(uid: anonymousUid!, isAnonymous: true);
    _controller.add(_current);
  }

  @override
  Future<AuthTokens?> tokens() async {
    calls.add('tokens');
    if (_current == null) return null;
    return AuthTokens(
        idToken: 'id-token-for-${_current!.uid}',
        appCheckToken: 'app-check-token');
  }

  @override
  Future<void> signIn() async {
    calls.add('signIn');
    final unexpected = throwNextSignIn;
    if (unexpected != null) {
      throwNextSignIn = null;
      throw unexpected;
    }
    final failure = failNextSignIn;
    if (failure != null) {
      failNextSignIn = null;
      throw AccountError(failure);
    }

    final wasAnonymous = _current?.isAnonymous ?? false;

    if (wasAnonymous && nextSignInFindsAnExistingAccount) {
      // No linking to do: they already had this Google account. The
      // anonymous uid and its counter go, which is the whole of the
      // migration, because an anonymous installation cannot save.
      nextSignInFindsAnExistingAccount = false;
      calls.add('discardAnonymous');
      _current = const Account(
          uid: 'uid-existing', email: 'name@example.com');
    } else if (wasAnonymous) {
      // LINKED. The same uid, which is what carries the free valuations
      // already counted across the sign-in.
      calls.add('link');
      _current = Account(uid: _current!.uid, email: 'name@example.com');
    } else {
      _current = const Account(uid: 'uid-test', email: 'name@example.com');
    }
    _controller.add(_current);
  }

  @override
  Future<void> signOut() async {
    calls.add('signOut');
    final hold = holdSignOut;
    if (hold != null) {
      holdSignOut = null;
      await hold.future;
    }
    if (signOutThrowsAfterClearing) {
      signOutThrowsAfterClearing = false;
      _current = _backToAnonymous();
      throw StateError('signed out, then failed');
    }
    final unexpected = throwNextSignOut;
    if (unexpected != null) {
      throwNextSignOut = null;
      throw unexpected;
    }
    _current = _backToAnonymous();
    _controller.add(_current);
  }

  /// Signing out lands on the anonymous account, not on nothing.
  ///
  /// Faithful to the real service, which signs back in anonymously the
  /// moment Firebase reports nobody: the app has to keep working signed
  /// out, and the free allowance has to keep being counted against an
  /// installation rather than an address. A fake that emitted null would
  /// test a state that cannot happen.
  Account _backToAnonymous() =>
      Account(uid: anonymousUid ??= 'anon-install-1', isAnonymous: true);

  @override
  Future<void> deleteAccount() async {
    calls.add('deleteAccount');
    final hold = holdDelete;
    if (hold != null) {
      holdDelete = null;
      await hold.future;
    }
    final failure = failNextDelete;
    if (failure != null) {
      failNextDelete = null;
      // The suffix belongs to whoever threw, not to the screen: only the
      // thing that ran the deletion knows how far it got. The real
      // service appends the matching sentence; the fake clears nothing
      // before failing, so for it the honest one is this.
      throw AccountError('$failure Nothing has been removed.');
    }
    stored.clear();
    // A deleted account cannot be linked to again, so the installation
    // gets a NEW anonymous identity - and, with it, a fresh allowance.
    // That is not a loophole so much as the honest consequence of
    // deleting everything we hold about someone.
    anonymousUid = 'anon-install-after-deletion';
    _current = _backToAnonymous();
    _controller.add(_current);
  }

  /// Signed out by something that is not this screen: a second device, a
  /// revoked token, a deletion elsewhere. The screens must follow it.
  void signedOut() {
    _current = _backToAnonymous();
    _controller.add(_current);
  }

  @override
  Future<Map<String, Object?>> exportData() async {
    calls.add('exportData');
    return Map.of(stored);
  }

  /// What the counter says. Settable so a test can put the screen in the
  /// state where it has a number, and in the state where it has none.
  int? countedValuations = 18;

  @override
  Future<int?> valuationsCounted() async {
    calls.add('valuationsCounted');
    return countedValuations;
  }

  @override
  void dispose() => _controller.close();
}
