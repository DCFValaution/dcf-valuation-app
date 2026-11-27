/// The deletion page, and the route that reaches it.
///
/// Google Play requires that an app offering accounts also offers deletion,
/// and that there is a way to ask from OUTSIDE the app: somebody who has
/// already uninstalled must still be able to get their data removed.
/// Asking them to reinstall in order to ask is precisely what the rule
/// exists to prevent, so the route has to work on a cold web visit with no
/// session, which is what most of these tests arrange.
///
/// The property the whole file is built around: a failed deletion says
/// nothing was removed. A person who believes their data is gone when it
/// is not has been misled about the one thing this page exists to do, and
/// that is worse than an error message.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/delete_account_page.dart';
import 'package:dcf_app/delete_account_route.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/theme.dart';

Future<void> pumpRoute(WidgetTester tester, AccountService service) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildDarkTheme(),
      home: DeleteAccountRoute(service: service),
    ),
  );
  await tester.pump();
}

void main() {
  group('the path', () {
    test('reaches the page', () {
      expect(isDeleteRoute('/delete'), isTrue);
    });

    test('is forgiving of what gets typed off a privacy policy', () {
      // This URL is read aloud, copied out of an email and typed by hand
      // far more often than it is clicked.
      expect(isDeleteRoute('/delete/'), isTrue);
      expect(isDeleteRoute('/DELETE'), isTrue);
      expect(isDeleteRoute('  /delete  '), isTrue);
    });

    test('does not swallow the rest of the app', () {
      for (final path in ['/', '/valuation', '/deleted', '/delete-me', '']) {
        expect(isDeleteRoute(path), isFalse, reason: path);
      }
    });

    testWidgets('opening it shows the deletion page, not the valuation screen',
        (tester) async {
      await tester.pumpWidget(const DcfApp(path: '/delete'));
      await tester.pump();

      expect(find.byKey(const Key('delete-title')), findsOneWidget);
      expect(find.byType(TextField), findsNothing,
          reason: 'no ticker search: this is not the app');
    });

    testWidgets('every other path is the app', (tester) async {
      await tester.pumpWidget(const DcfApp(path: '/'));
      await tester.pump();

      expect(find.byKey(const Key('delete-title')), findsNothing);
    });
  });

  group('arriving signed out', () {
    testWidgets('asks who you are, and says why', (tester) async {
      await pumpRoute(tester, FakeAccountService());

      expect(find.byKey(const Key('delete-sign-in')), findsOneWidget);
      expect(find.textContaining('whose account to delete'), findsOneWidget);
      expect(find.byKey(const Key('delete-confirm')), findsNothing,
          reason: 'nothing to confirm until we know whose account it is');
    });

    testWidgets('states the consequences before the sign-in, not after',
        (tester) async {
      // A consequence disclosed only after the irreversible action is not
      // a disclosure. It is shown in both states for that reason.
      await pumpRoute(tester, FakeAccountService());

      expect(find.text('What this removes'), findsOneWidget);
      expect(find.textContaining('cannot be undone'), findsOneWidget);
      expect(find.textContaining('keeps working without an account'),
          findsOneWidget);
    });

    testWidgets('signing in moves to the confirmation', (tester) async {
      final service = FakeAccountService();
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-sign-in')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('delete-confirm')), findsOneWidget);
      expect(find.textContaining('name@example.com'), findsOneWidget,
          reason: 'a person with two Google accounts must see which one');
    });

    testWidgets('dismissing the Google sheet is not an error', (tester) async {
      // They changed their mind. The page already says what to do next,
      // and a red box would be the app telling them off for it.
      final service = FakeAccountService()..failNextSignIn = cancelled;
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-sign-in')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('delete-sign-in')), findsOneWidget);
      expect(find.textContaining('could not'), findsNothing);
    });
  });

  group('arriving signed in', () {
    testWidgets('goes straight to the confirmation', (tester) async {
      await pumpRoute(
        tester,
        FakeAccountService(
            signedIn: const Account(uid: 'uid-1', email: 'a@example.com')),
      );

      expect(find.byKey(const Key('delete-confirm')), findsOneWidget);
      expect(find.textContaining('a@example.com'), findsOneWidget);
    });

    testWidgets('deleting says so, plainly', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'));
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pumpAndSettle();

      expect(service.calls, contains('deleteAccount'));
      expect(find.textContaining('Your account is gone'), findsOneWidget);
      expect(find.textContaining('30 days'), findsOneWidget,
          reason: 'the backup window is part of the promise');
      expect(find.byKey(const Key('delete-confirm')), findsNothing,
          reason: 'nothing left to delete');
    });

    testWidgets('the button goes inert while the request is out',
        (tester) async {
      // Tapping twice on a destructive action is how a person ends up
      // reading a failure for something that already succeeded.
      final hold = Completer<void>();
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..holdDelete = hold;
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pump();

      final button =
          tester.widget<FilledButton>(find.byKey(const Key('delete-confirm')));
      expect(button.onPressed, isNull);
      expect(find.text('Deleting…'), findsOneWidget);

      hold.complete();
      await tester.pumpAndSettle();
      expect(service.calls.where((c) => c == 'deleteAccount').length, 1);
    });
  });

  group('when it fails', () {
    testWidgets('it says nothing was removed', (tester) async {
      // The property this whole file is built around.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..failNextDelete = 'the network dropped';
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Nothing has been removed'), findsOneWidget);
      expect(find.textContaining('Your account is gone'), findsNothing);
    });

    testWidgets('the page does not assert it ITSELF', (tester) async {
      // It used to print "Nothing has been removed" as its own fixed
      // copy, under every failure. The deletion removes documents before
      // the auth user, so a failure at the second step leaves the data
      // already gone - and the page would have denied it, about the one
      // action that cannot be undone. Only whoever ran the deletion knows
      // how far it got, so the sentence comes from the error.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..failNextDelete = 'Everything you saved has in fact been deleted;';
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pumpAndSettle();

      expect(find.textContaining('has in fact been deleted'), findsOneWidget);
      expect(find.textContaining('still there and still yours'), findsNothing,
          reason: 'the page must not contradict the only thing that knows');
    });

    testWidgets('it offers another go rather than a dead end', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..failNextDelete = 'the network dropped';
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('delete-retry')), findsOneWidget);

      await tester.tap(find.byKey(const Key('delete-retry')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Your account is gone'), findsOneWidget,
          reason: 'the second attempt succeeds, and the page moves on');
    });

    testWidgets('it shows what went wrong without showing a stack trace',
        (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..failNextDelete = 'the network dropped';
      await pumpRoute(tester, service);

      await tester.tap(find.byKey(const Key('delete-confirm')));
      await tester.pumpAndSettle();

      expect(find.textContaining('the network dropped'), findsOneWidget);
      expect(find.textContaining('PERMISSION_DENIED'), findsNothing);
      expect(find.textContaining('Exception'), findsNothing);
    });
  });
}
