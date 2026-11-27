/// Sign-in that does not succeed, in every way it can fail.
///
/// THE BUG THIS EXISTS FOR
/// -----------------------
/// `google_sign_in_web` reports `supportsAuthenticate() => false` and its
/// `authenticate()` throws:
///
///     UnimplementedError('authenticate is not supported on the web.
///     Instead, use renderButton to create a sign-in widget.')
///
/// The sheet caught only `AccountError`, so that escaped the handler,
/// `_busy` was never cleared, and the button read "Signing in…" for as
/// long as anyone was willing to wait — no Google window, nothing on
/// screen to say why, and no way back except closing the sheet.
///
/// A spinner that cannot stop is the worst failure a button has, because
/// it is indistinguishable from slowness: the reader has no way to tell
/// whether to wait or to give up.
///
/// So the rule these tests enforce is blunt: WHATEVER is thrown, the
/// spinner stops and the button comes back. Not "whatever we expect".
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/delete_account_route.dart';
import 'package:dcf_app/sign_in_sheet.dart';
import 'package:dcf_app/theme.dart';

/// Opens the sheet the way the save action does.
Future<void> pumpSheet(WidgetTester tester, AccountService service) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildDarkTheme(),
      home: Scaffold(
        body: Builder(
          builder: (context) => Center(
            child: FilledButton(
              onPressed: () => requireAccount(context, service,
                  reason: 'Save this valuation'),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

final _google = find.byKey(const Key('sign-in-google'));

bool spinning(WidgetTester tester) =>
    tester.widget<FilledButton>(_google).onPressed == null;

void main() {
  group('the sheet always stops spinning', () {
    testWidgets('when sign-in throws something unexpected', (tester) async {
      // THE regression test. UnimplementedError is exactly what
      // google_sign_in threw on the web, and it is not an AccountError.
      final service = FakeAccountService()
        ..throwNextSignIn = UnimplementedError('authenticate is not '
            'supported on the web.');
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();

      expect(spinning(tester), isFalse,
          reason: 'the button has to come back, whatever was thrown');
      expect(find.text('Continue with Google'), findsOneWidget);
      expect(find.byKey(const Key('sign-in-error')), findsOneWidget);
    });

    testWidgets('and says something a person can act on', (tester) async {
      // Not the exception's own text: "UnimplementedError: authenticate
      // is not supported on the web" tells a reader nothing they can do.
      final service = FakeAccountService()
        ..throwNextSignIn = UnimplementedError('authenticate is not '
            'supported on the web.');
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();

      expect(find.textContaining('did not finish'), findsOneWidget);
      expect(find.textContaining('try again'), findsOneWidget);
      expect(find.textContaining('UnimplementedError'), findsNothing);
      expect(find.textContaining('renderButton'), findsNothing);
    });

    testWidgets('when a pop-up blocker refuses the window', (tester) async {
      final service = FakeAccountService()
        ..failNextSignIn = 'Your browser blocked the sign-in window.';
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();

      expect(spinning(tester), isFalse);
      expect(find.textContaining('blocked the sign-in window'),
          findsOneWidget);
    });

    testWidgets('when the person closes the window', (tester) async {
      // A cancellation is not a failure: no red text, just the button
      // back the way it was.
      final service = FakeAccountService()..failNextSignIn = cancelled;
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();

      expect(spinning(tester), isFalse);
      expect(find.byKey(const Key('sign-in-error')), findsNothing);
      expect(find.text('Continue with Google'), findsOneWidget);
    });

    testWidgets('and a second attempt can still succeed', (tester) async {
      // A failure that leaves the sheet unusable is only half-fixed.
      final service = FakeAccountService()
        ..throwNextSignIn = StateError('something odd');
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();
      expect(spinning(tester), isFalse);

      await tester.tap(_google);
      await tester.pumpAndSettle();

      expect(service.current, isNotNull, reason: 'the retry signed in');
      expect(find.byKey(const Key('sign-in-google')), findsNothing,
          reason: 'the sheet closed on success');
    });

    testWidgets('"Not now" still works after a failure', (tester) async {
      final service = FakeAccountService()
        ..throwNextSignIn = StateError('something odd');
      await pumpSheet(tester, service);

      await tester.tap(_google);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('sign-in-not-now')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('sign-in-google')), findsNothing);
      expect(service.current, isNull);
    });
  });

  group('the deletion page survives the same thing', () {
    // It had the identical handler, and the identical hole in it.
    testWidgets('an unexpected throw leaves it usable', (tester) async {
      final service = FakeAccountService()
        ..throwNextSignIn = UnimplementedError('not supported on the web');
      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: DeleteAccountRoute(service: service),
      ));
      await tester.pump();

      await tester.tap(find.byKey(const Key('delete-sign-in')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('delete-sign-in')), findsOneWidget,
          reason: 'still offering the only action the page has');
      expect(find.textContaining('did not finish'), findsOneWidget);
    });

    testWidgets('a closed window says nothing at all', (tester) async {
      final service = FakeAccountService()..failNextSignIn = cancelled;
      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: DeleteAccountRoute(service: service),
      ));
      await tester.pump();

      await tester.tap(find.byKey(const Key('delete-sign-in')));
      await tester.pumpAndSettle();

      expect(find.textContaining('did not finish'), findsNothing);
      expect(find.byKey(const Key('delete-sign-in')), findsOneWidget);
    });
  });
}
