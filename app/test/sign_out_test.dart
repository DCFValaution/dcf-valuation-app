/// Sign out must happen, and must be visible happening.
///
/// THE BUG
/// -------
/// Tapping "Sign out" on the web showed its tap animation and then
/// nothing: no spinner, no message, no console error, and the email still
/// on screen. Two causes, stacked:
///
///   1. The service awaited `GoogleSignIn.instance.signOut()` BEFORE
///      `FirebaseAuth.signOut()`. On the web that future never completes,
///      and the `catch` around it was no help at all - a catch handles a
///      throw, not a hang. The Firebase sign-out never ran.
///   2. The screen's handler was four lines with no busy flag, no try and
///      no catch, so there was no state in which it could have said so
///      even if it had known.
///
/// Both are tested here, and the first is tested on the real function
/// rather than a restatement of it: `signOutInOrder` takes the two steps
/// as arguments precisely so a test can hang one and prove the other ran.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/account_screen.dart';
import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/sign_out_sequence.dart';
import 'package:dcf_app/theme.dart';

Widget wrap(Widget child) =>
    MaterialApp(theme: buildDarkTheme(), home: child);

/// The account screen with somewhere to go when it leaves, so "did it
/// leave?" is an observable fact rather than an assumption.
Widget withRoute(FakeAccountService service, {VoidCallback? onLeave}) =>
    MaterialApp(
      theme: buildDarkTheme(),
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => AccountScreen(
                  service: service,
                  onSignedOut: () {
                    onLeave?.call();
                    Navigator.of(context).maybePop();
                  },
                ),
              )),
              child: const Text('open account'),
            ),
          ),
        ),
      ),
    );

Future<void> openAccount(WidgetTester tester) async {
  await tester.tap(find.text('open account'));
  await tester.pumpAndSettle();
}

void main() {
  group('the order of a sign-out', () {
    test('Firebase goes first, so a later hang cannot undo it', () async {
      final order = <String>[];
      final hanging = Completer<void>(); // never completed, like the web

      await signOutInOrder(
        firebase: () async => order.add('firebase'),
        googleSession: () {
          order.add('google started');
          return hanging.future;
        },
        googleTimeout: const Duration(milliseconds: 20),
      );

      expect(order, ['firebase', 'google started'],
          reason: 'the shipped version had these the other way round, and '
              'so never reached the first one');
    });

    test('a hanging Google sign-out returns anyway', () async {
      final hanging = Completer<void>();
      var firebaseDone = false;

      // No `expect(..., completes)` games: if this await does not return,
      // the test times out, which is exactly the failure being prevented.
      await signOutInOrder(
        firebase: () async => firebaseDone = true,
        googleSession: () => hanging.future,
        googleTimeout: const Duration(milliseconds: 20),
      );

      expect(firebaseDone, isTrue);
      expect(hanging.isCompleted, isFalse,
          reason: 'it is still hanging; we simply stopped waiting');
    });

    test('a throwing Google sign-out does not reach the caller', () async {
      var firebaseDone = false;
      await signOutInOrder(
        firebase: () async => firebaseDone = true,
        googleSession: () async => throw UnimplementedError('web'),
      );
      expect(firebaseDone, isTrue);
    });

    test('a failing Firebase sign-out DOES reach the caller', () async {
      // The opposite rule, and the reason the two steps are not treated
      // alike: if the thing that matters failed, the screen must not
      // claim the person is signed out.
      await expectLater(
        signOutInOrder(
          firebase: () async => throw StateError('network'),
          googleSession: () async {},
        ),
        throwsStateError,
      );
    });

    test('no Google step at all is a sign-out, not a no-op', () async {
      var firebaseDone = false;
      await signOutInOrder(firebase: () async => firebaseDone = true);
      expect(firebaseDone, isTrue);
    });

    test('bestEffort swallows a hang as well as a throw', () async {
      final hanging = Completer<void>();
      await bestEffort('test', () => hanging.future,
          timeout: const Duration(milliseconds: 20));
      await bestEffort('test', () async => throw StateError('x'));
      // Reaching here is the assertion.
    });
  });

  group('the sign-out button', () {
    testWidgets('shows a spinner while it is working', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      final hold = Completer<void>();
      service.holdSignOut = hold;

      await tester.pumpWidget(wrap(AccountScreen(service: service)));
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pump();

      expect(find.byType(CircularProgressIndicator), findsOneWidget,
          reason: 'the whole complaint was that it looked like nothing '
              'happened');
      expect(find.text('Signing out…'), findsOneWidget);

      hold.complete();
      await tester.pumpAndSettle();
    });

    testWidgets('cannot be tapped twice', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      final hold = Completer<void>();
      service.holdSignOut = hold;

      await tester.pumpWidget(wrap(AccountScreen(service: service)));
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pump();
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pump();

      expect(service.calls.where((c) => c == 'signOut').length, 1);

      hold.complete();
      await tester.pumpAndSettle();
    });

    testWidgets('leaves the screen when it succeeds', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      var left = false;

      await tester.pumpWidget(withRoute(service, onLeave: () => left = true));
      await openAccount(tester);
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pumpAndSettle();

      expect(left, isTrue);
      expect(find.text('open account'), findsOneWidget,
          reason: 'back where it started');
    });

    testWidgets('leaves when the auth state changes, not when the call '
        'returns', (tester) async {
      // The property that makes the screen immune to the next version of
      // this bug: whatever path signs the person out, the screen follows.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      var left = false;

      await tester.pumpWidget(withRoute(service, onLeave: () => left = true));
      await openAccount(tester);
      expect(find.byKey(const Key('account-email')), findsOneWidget);

      // Nobody tapped anything. The account simply went away - a second
      // device signing out, or a token revoked.
      service.signedOut();
      await tester.pumpAndSettle();

      expect(left, isTrue);
    });

    testWidgets('says so, with a retry, when it fails', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      service.throwNextSignOut = StateError('no network');

      await tester.pumpWidget(wrap(AccountScreen(service: service)));
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pumpAndSettle();

      expect(find.byType(CircularProgressIndicator), findsNothing);
      expect(find.byKey(const Key('account-sign-out-error')), findsOneWidget);
      expect(find.byKey(const Key('account-sign-out-retry')), findsOneWidget);
    });

    testWidgets('the retry really retries', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      service.throwNextSignOut = StateError('no network');

      await tester.pumpWidget(wrap(AccountScreen(service: service)));
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pumpAndSettle();

      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out-retry')));
      await tester.tap(find.byKey(const Key('account-sign-out-retry')));
      await tester.pumpAndSettle();

      expect(service.calls.where((c) => c == 'signOut').length, 2);
      // Not null: signing out lands on the anonymous account, because
      // the app has to keep working signed out and the free allowance has
      // to keep being counted against an installation rather than an
      // address. `signedIn` is the question that still means what it did.
      expect(service.signedIn, isFalse);
      expect(service.current!.isAnonymous, isTrue);
    });

    testWidgets('leaves exactly once when both paths fire', (tester) async {
      // Both the auth listener and the handler call _leave(), and either
      // can be first. Without the once-only guard this pops twice, which
      // takes the screen BEHIND the account screen with it - a bug that
      // looks like the app randomly going back two steps.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      final hold = Completer<void>();
      service.holdSignOut = hold;
      var leaves = 0;

      await tester.pumpWidget(withRoute(service, onLeave: () => leaves++));
      await openAccount(tester);
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pump();

      // The auth state arrives first (the listener leaves), then the call
      // itself returns (the handler would leave again).
      hold.complete();
      await tester.pumpAndSettle();

      expect(leaves, 1);
    });

    testWidgets('a failure that signed them out anyway still leaves',
        (tester) async {
      // A thrown error does not mean the person is still signed in. This
      // is the sign-in sheet's bug in reverse, and it would read to the
      // person as "it failed" under a screen that no longer has an
      // account on it.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'name@example.com'));
      service.signOutThrowsAfterClearing = true;
      var left = false;

      await tester.pumpWidget(withRoute(service, onLeave: () => left = true));
      await openAccount(tester);
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-sign-out')));
      await tester.tap(find.byKey(const Key('account-sign-out')));
      await tester.pumpAndSettle();

      expect(left, isTrue);
      expect(find.byKey(const Key('account-sign-out-error')), findsNothing);
    });
  });
}
