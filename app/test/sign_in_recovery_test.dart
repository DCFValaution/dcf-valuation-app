/// Signing in when the reader ALREADY HAS the Google account.
///
/// THE BUG THESE EXIST FOR
/// -----------------------
/// A returning reader signed in from the anonymous account. The link
/// threw `credential-already-in-use`, which is the expected answer. The
/// recovery then opened a SECOND pop-up, with no user gesture behind it
/// because the first had closed, so the browser blocked it. The outer
/// handler read that `popup-blocked` as a genuine blocked pop-up and
/// answered with a full-page redirect, which could not complete either.
/// The reader proved their identity to Google twice and came back
/// anonymous with their free valuations reset - because the counter had
/// already been deleted on the way to the pop-up that never opened.
///
/// Four properties, and the last is the one a reader would notice:
///
///   1. the credential comes out of the error, so no second pop-up;
///   2. a pop-up the app should not have opened is not evidence that
///      pop-ups are blocked;
///   3. without a credential there is nothing to recover, and the answer
///      is to stop;
///   4. stopping COSTS NOTHING - same uid, same allowance.
library;

import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/sign_in_recovery.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('after a link attempt fails', () {
    test('an existing account is recovered from the error credential',
        () async {
      expect(
        recoveryAfterLinkFailure('credential-already-in-use',
            haveCredential: true),
        LinkRecovery.signInWithTheCredential,
        reason: 'the error carries the proof; asking again is what broke',
      );
    });

    test('every "you already have this" code recovers the same way', () {
      for (final code in const [
        'credential-already-in-use',
        'email-already-in-use',
        'account-exists-with-different-credential',
        'provider-already-linked',
      ]) {
        expect(alreadyHasAnAccount(code), isTrue, reason: code);
        expect(recoveryAfterLinkFailure(code, haveCredential: true),
            LinkRecovery.signInWithTheCredential,
            reason: code);
      }
    });

    test('WITHOUT a credential it stops rather than asking again', () {
      // The whole bug in one assertion. The only way to proceed without a
      // credential is a second gesture the code does not have, and
      // reaching for one is what cost a reader their allowance.
      expect(
        recoveryAfterLinkFailure('credential-already-in-use',
            haveCredential: false),
        LinkRecovery.reportAndKeepEverything,
      );
    });

    test('a cancelled pop-up keeps everything, credential or not', () {
      for (final have in const [true, false]) {
        expect(
          recoveryAfterLinkFailure('popup-closed-by-user',
              haveCredential: have),
          LinkRecovery.reportAndKeepEverything,
          reason: 'haveCredential: $have',
        );
      }
    });

    test('an unrecognised code keeps everything', () {
      expect(
        recoveryAfterLinkFailure('network-request-failed',
            haveCredential: true),
        LinkRecovery.reportAndKeepEverything,
      );
    });
  });

  group('classifying a pop-up failure', () {
    test('shutting the window is not a failure', () {
      for (final code in const [
        'popup-closed-by-user',
        'cancelled-popup-request',
        'user-cancelled',
      ]) {
        expect(classifyPopupFailure(code), PopupFailure.cancelled,
            reason: code);
      }
    });

    test('only popup-blocked is a blocked pop-up', () {
      expect(classifyPopupFailure('popup-blocked'), PopupFailure.blocked);
    });

    test('the three codes that used to trigger a redirect no longer do', () {
      // These were answered with signInWithRedirect, which cannot work on
      // this deployment at all: the app is not on Firebase Hosting while
      // its authDomain is, and browsers that block third-party storage
      // break that handshake. They are ordinary failures now.
      for (final code in const [
        'web-storage-unsupported',
        'internal-error',
        'timeout',
      ]) {
        expect(classifyPopupFailure(code), PopupFailure.other, reason: code);
        expect(classifyPopupFailure(code), isNot(PopupFailure.blocked),
            reason: '$code is not a blocked pop-up and never was');
      }
    });

    test('an unauthorised domain gets its own remedy, not "try again"', () {
      expect(classifyPopupFailure('unauthorized-domain'),
          PopupFailure.unauthorisedDomain);
      expect(unauthorisedDomainMessage, contains('Authorised domains'));
    });

    test('the blocked message names the setting to change', () {
      // "Try again" would be advice to repeat the thing that cannot work.
      expect(popupBlockedMessage.toLowerCase(), contains('pop-up'));
      expect(popupBlockedMessage.toLowerCase(), contains('allow'));
    });
  });

  group('a sign-in that does not finish', () {
    test('leaves the anonymous uid exactly as it was', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final before = service.current!.uid;

      service.failNextSignIn = 'nope';
      await expectLater(service.signIn(), throwsA(isA<AccountError>()));

      expect(service.current, isNotNull);
      expect(service.current!.isAnonymous, isTrue);
      expect(service.current!.uid, before,
          reason: 'a failed sign-in must not cost the reader their identity');
    });

    test('leaves it alone when the reader simply cancels', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final before = service.current!.uid;

      service.throwNextSignIn = const AccountError(cancelled);
      await expectLater(service.signIn(), throwsA(anything));

      expect(service.current!.isAnonymous, isTrue);
      expect(service.current!.uid, before);
      expect(service.signedIn, isFalse);
    });
  });
}
