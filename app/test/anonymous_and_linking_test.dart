/// Every installation is signed in, and signing in properly LINKS.
///
/// Two properties, and the first one changes what "signed in" means
/// everywhere else in the app:
///
///   - `current != null` is now true for a reader who has never signed in
///     to anything, so the question the screens must ask is `signedIn`.
///   - Signing in from the anonymous account KEEPS THE SAME uid, which is
///     what carries the free valuations already counted across. If it did
///     not, a reader who signed in at the gate would arrive on the other
///     side with their three spent and no way to get them back.
library;

import 'package:dcf_app/account_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('an installation with no account', () {
    test('is signed in anonymously, but is not signedIn', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();

      expect(service.current, isNotNull,
          reason: 'there is an account; it is just not a real one');
      expect(service.current!.isAnonymous, isTrue);
      expect(service.signedIn, isFalse,
          reason: 'the screens must not treat this as having an account');
      expect(service.anonymousAccount, isNotNull);
    });

    test('has a uid, which is the whole point of it', () async {
      // Without one, the free allowance could only be counted per IP
      // address - and an address is shared by a household, an office or a
      // whole campus.
      final service = FakeAccountService();
      await service.ensureSignedIn();
      expect(service.current!.uid, isNotEmpty);
    });

    test('is created once, not on every call', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final first = service.current!.uid;
      await service.ensureSignedIn();
      expect(service.current!.uid, first);
    });

    test('has tokens to send', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final tokens = await service.tokens();
      expect(tokens, isNotNull);
      expect(tokens!.headers['Authorization'], isNotNull);
      expect(tokens.headers['X-Firebase-AppCheck'], isNotNull);
    });

    test('offers no tokens when there is no account at all', () async {
      expect(await FakeAccountService().tokens(), isNull);
    });
  });

  group('signing in from the anonymous account', () {
    test('KEEPS THE UID, so the counted valuations come too', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final anonymousUid = service.current!.uid;

      await service.signIn();

      expect(service.signedIn, isTrue);
      expect(service.current!.isAnonymous, isFalse);
      expect(service.current!.uid, anonymousUid,
          reason: 'linking keeps the uid; signing in would strand the count');
      expect(service.calls, contains('link'));
    });

    test('does not link when there was no anonymous account', () async {
      final service = FakeAccountService();
      await service.signIn();
      expect(service.calls, isNot(contains('link')));
      expect(service.signedIn, isTrue);
    });
  });

  group('when that Google account already exists', () {
    test('signs in to it and discards the anonymous one', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final anonymousUid = service.current!.uid;
      service.nextSignInFindsAnExistingAccount = true;

      await service.signIn();

      expect(service.signedIn, isTrue);
      expect(service.current!.uid, isNot(anonymousUid),
          reason: 'there was nothing to link to');
      expect(service.calls, contains('discardAnonymous'),
          reason: 'its counter must go, or it is data we said we deleted');
      expect(service.calls, isNot(contains('link')));
    });

    test('the discard happens before the identity changes', () async {
      // Order, not merely occurrence. After the switch, the rules will not
      // let anybody reach the old uid's documents again - so a tidy-up
      // scheduled for afterwards can never run.
      final service = FakeAccountService();
      await service.ensureSignedIn();
      service.nextSignInFindsAnExistingAccount = true;
      await service.signIn();

      final discarded = service.calls.indexOf('discardAnonymous');
      expect(discarded, greaterThanOrEqualTo(0));
      expect(service.calls.sublist(discarded), isNot(contains('link')));
    });
  });

  group('signing out', () {
    test('lands back on an anonymous account, not on nothing', () async {
      // The app has to keep working signed out, and the allowance has to
      // keep being counted against an installation rather than an address.
      final service = FakeAccountService();
      await service.ensureSignedIn();
      await service.signIn();
      await service.signOut();

      expect(service.current, isNotNull);
      expect(service.current!.isAnonymous, isTrue);
      expect(service.signedIn, isFalse);
    });

    test('emits that state to anybody watching', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      await service.signIn();

      final seen = <Account?>[];
      final sub = service.changes.listen(seen.add);
      await service.signOut();
      await Future<void>.delayed(Duration.zero);
      await sub.cancel();

      expect(seen.last!.isAnonymous, isTrue);
    });
  });

  group('deleting the account', () {
    test('leaves a NEW anonymous identity, not the old one', () async {
      final service = FakeAccountService();
      await service.ensureSignedIn();
      final before = service.current!.uid;
      await service.signIn();
      await service.deleteAccount();

      expect(service.signedIn, isFalse);
      expect(service.current!.isAnonymous, isTrue);
      expect(service.current!.uid, isNot(before),
          reason: 'a deleted account cannot be linked to again');
    });
  });
}
