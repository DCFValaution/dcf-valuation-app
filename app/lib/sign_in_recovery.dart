/// The decisions a Google sign-in has to make when it does not go cleanly.
///
/// These are pure functions in their own file for one reason: the real
/// sign-in lives in `firebase_account_service.dart`, which cannot be
/// imported by a unit test without pulling in the Firebase plugins. The
/// decisions were therefore untested, and one of them shipped a bug that
/// cost a reader their free valuations. They are separated here so a test
/// can reach them.
///
/// THE BUG THIS EXISTS FOR
/// -----------------------
/// A reader who already had a Google account signed in from the anonymous
/// account. `linkWithPopup` threw `credential-already-in-use`, which is
/// correct and expected. The recovery then called `signInWithPopup` again
/// - a SECOND pop-up, with no user gesture behind it, because the first
/// one had already closed. Browsers block that, so it threw
/// `popup-blocked`, which the outer handler read as a genuine blocked
/// pop-up and answered with a full-page redirect. The redirect could not
/// complete either. The reader came back anonymous, with their allowance
/// reset, having proved their identity to Google twice.
///
/// Three separate faults, and the fix for each is here:
///
///   1. The error carries the credential. There is never a reason to ask
///      for a second gesture - [LinkRecovery.signInWithTheCredential].
///   2. A pop-up the app should never have opened is not evidence that
///      pop-ups are blocked. Only a genuine [PopupFailure.blocked] is.
///   3. The anonymous account's counter must not be touched until the
///      moment it is certain to be replaced.
library;

/// What to do after `linkWithPopup` / `linkWithCredential` has failed.
enum LinkRecovery {
  /// They already hold this Google account, and the error handed us the
  /// credential to sign in with. The anonymous identity is replaced.
  signInWithTheCredential,

  /// Nothing can be recovered. Report it and TOUCH NOTHING: the anonymous
  /// uid, its counter and its markers all stay exactly as they were, so a
  /// reader who changed their mind - or whose sign-in simply failed - is
  /// left no worse off than before they tried.
  reportAndKeepEverything,
}

/// Which of the codes mean "you already have an account with this".
///
/// `provider-already-linked` is in the list because it is the same
/// situation seen from the other side: whatever the anonymous account
/// thinks, the reader's identity already exists elsewhere.
bool alreadyHasAnAccount(String code) =>
    code == 'credential-already-in-use' ||
    code == 'email-already-in-use' ||
    code == 'account-exists-with-different-credential' ||
    code == 'provider-already-linked';

/// What a failed link attempt should do next.
///
/// [haveCredential] is whether the error (or the caller) left us a
/// credential to sign in with. Without one the only way to proceed would
/// be to ask the reader for another gesture, which is the thing that
/// broke, so the answer is to stop - and stopping must cost them nothing.
LinkRecovery recoveryAfterLinkFailure(String code,
        {required bool haveCredential}) =>
    alreadyHasAnAccount(code) && haveCredential
        ? LinkRecovery.signInWithTheCredential
        : LinkRecovery.reportAndKeepEverything;

/// How a pop-up sign-in ended, where it did not end in being signed in.
enum PopupFailure {
  /// They shut the window, or a second attempt superseded the first.
  /// Not a failure, and not worth a red message.
  cancelled,

  /// The browser refused to open the window. The one case where telling
  /// somebody to change a setting is useful advice rather than a shrug.
  blocked,

  /// The address is not on the project's authorised list. A precise
  /// remedy, so it gets a precise message instead of "try again".
  unauthorisedDomain,

  /// Everything else, which the reader sees as the general notice.
  other,
}

/// Classify a `FirebaseAuthException` code from the pop-up path.
///
/// `web-storage-unsupported`, `internal-error` and `timeout` were once
/// treated as blocked pop-ups and answered with a redirect. They are not
/// blocked pop-ups, and the redirect is gone - see the note on the web
/// sign-in path for why it could never have worked on this deployment -
/// so they are [PopupFailure.other] and get the general notice.
PopupFailure classifyPopupFailure(String code) {
  switch (code) {
    case 'popup-closed-by-user':
    case 'cancelled-popup-request':
    case 'user-cancelled':
      return PopupFailure.cancelled;
    case 'popup-blocked':
      return PopupFailure.blocked;
    case 'unauthorized-domain':
      return PopupFailure.unauthorisedDomain;
    default:
      return PopupFailure.other;
  }
}

/// What the reader is told when the browser refused the sign-in window.
const String popupBlockedMessage =
    'Your browser blocked the sign-in window. Allow pop-ups for this site '
    'and try again.';

/// What the reader is told when the address is not authorised.
const String unauthorisedDomainMessage =
    'This address is not an authorised domain for sign-in. Add it in '
    'Firebase console → Authentication → Settings → Authorised domains.';
