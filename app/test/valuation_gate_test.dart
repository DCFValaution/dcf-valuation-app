/// The gate, which must never be a dead end.
///
/// That is the entire specification, and it is the thing these tests
/// protect: in every state - ready, failed, offline, no account system at
/// all - there are two ways onward that need no network, and the sign-in
/// button is never the only thing on screen.
///
/// The second property is that this screen DECIDES NOTHING. It is built
/// from a `ValuationGated` the server sent, and the numbers on it are the
/// server's. There is no arithmetic here to get wrong.
library;

import 'dart:async';

import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';
import 'package:dcf_app/valuation_gate.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void _noop() {}

const gated = ValuationGated(
  message: 'You have used your 3 free valuations.',
  limit: 3,
  window: 'lifetime',
  signInLiftsLimit: true,
  isNetworkLimit: false,
);

Future<void> pumpGate(
  WidgetTester tester, {
  ValuationGated outcome = gated,
  AccountService? service,
  VoidCallback? onSignedIn,
  VoidCallback? onOpenSaved = _noop,
  VoidCallback? onBack,
}) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildLightTheme(),
    home: Scaffold(
      body: SingleChildScrollView(
        child: ValuationGateView(
          gated: outcome,
          service: service,
          onSignedIn: onSignedIn,
          onOpenSaved: onOpenSaved,
          onBack: onBack,
        ),
      ),
    ),
  ));
}

void main() {
  group('what it says', () {
    testWidgets('counts what has been used, from the server', (tester) async {
      await pumpGate(tester, service: FakeAccountService());
      expect(find.text('3 of 3 free valuations used'), findsOneWidget);
      expect(find.text('Carry on for free'), findsOneWidget);
    });

    testWidgets('shows the server message rather than one of its own',
        (tester) async {
      await pumpGate(tester, service: FakeAccountService());
      expect(find.text('You have used your 3 free valuations.'),
          findsOneWidget);
    });

    testWidgets('a daily limit is worded as today, not as for ever',
        (tester) async {
      await pumpGate(
        tester,
        service: FakeAccountService(),
        outcome: const ValuationGated(
          message: "You have used today's 3 valuations.",
          limit: 3,
          window: 'day',
          signInLiftsLimit: false,
          isNetworkLimit: false,
        ),
      );
      expect(find.text('3 of 3 valuations used today'), findsOneWidget);
    });

    testWidgets('does not name a data source we are not on', (tester) async {
      // A specific copy fix, kept as a test so it cannot quietly come
      // back: the app is not on SEC EDGAR yet, and naming it would be a
      // claim about where the numbers come from that is not true.
      await pumpGate(tester, service: FakeAccountService());
      final why = tester.widget<Text>(find.byKey(const Key('gate-why')));
      expect(why.data, isNot(contains('EDGAR')));
      expect(why.data, contains('company filings and market prices'));
      expect(why.data, isNot(contains('address shared by thousands')),
          reason: 'anonymous use is counted per installation, not per '
              'address - the old wording described something else');
    });

    testWidgets('the network cap is explained as the network, not as them',
        (tester) async {
      await pumpGate(
        tester,
        service: FakeAccountService(),
        outcome: const ValuationGated(
          message: 'This network has made a lot of signed-out valuations.',
          limit: 20,
          window: 'day',
          signInLiftsLimit: true,
          isNetworkLimit: true,
        ),
      );
      final why = tester.widget<Text>(find.byKey(const Key('gate-why')));
      expect(why.data, contains('shared by a household'));
    });

    testWidgets('and NEVER claims they used their own three', (tester) async {
      // The falsehood worth guarding: a reader on a Singapore carrier or
      // a school network can meet the per-network cap having valued
      // nothing at all. "20 of 20 valuations used today" would be a plain
      // lie told to somebody who has done nothing.
      await pumpGate(
        tester,
        service: FakeAccountService(),
        outcome: const ValuationGated(
          message: 'Free valuations from this network are used up for today.',
          limit: 20,
          window: 'day',
          signInLiftsLimit: true,
          isNetworkLimit: true,
        ),
      );
      final count = tester.widget<Text>(find.byKey(const Key('gate-count')));
      expect(count.data, 'Free valuations from this network are used up '
          'for today');
      expect(count.data, isNot(contains('20 of 20')));
      expect(find.byIcon(Icons.check_circle_rounded), findsNothing,
          reason: 'no ticks for an allowance they did not spend');
    });
  });

  group('never a dead end', () {
    testWidgets('offers two ways onward before anything is tried',
        (tester) async {
      await pumpGate(tester, service: FakeAccountService());
      expect(find.byKey(const Key('gate-open-saved')), findsOneWidget);
      expect(find.byKey(const Key('gate-back')), findsOneWidget);
      expect(find.byKey(const Key('sign-in-with-google')), findsOneWidget);
    });

    testWidgets('keeps them when signing in fails', (tester) async {
      final service = FakeAccountService()..failNextSignIn = 'Google said no.';
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-error')), findsOneWidget);
      expect(find.byKey(const Key('gate-open-saved')), findsOneWidget);
      expect(find.byKey(const Key('gate-back')), findsOneWidget);
    });

    testWidgets('keeps them when the reader is offline', (tester) async {
      final service = FakeAccountService()
        // The message carries NO offline keyword on purpose. If it said
        // "failed host lookup" the string check underneath would catch it
        // and the type-name branch above would never run - which is
        // exactly the hole mutation testing found here.
        ..throwNextSignIn = const SocketException('connection reset');
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-offline')), findsOneWidget);
      expect(find.text('You are offline'), findsOneWidget);
      expect(find.byKey(const Key('gate-open-saved')), findsOneWidget);
    });

    testWidgets('still recognises it when the class name is minified',
        (tester) async {
      // THE PRODUCTION SHAPE. dart2js renames classes in a release build,
      // so anything that compared `runtimeType.toString()` matched in
      // every test and never in the build that reaches readers - which is
      // exactly what happened, and was only visible in a screenshot of a
      // real release build.
      //
      // This type is named nothing useful. Only its message identifies
      // it, and messages are string literals, which survive minification.
      final service = FakeAccountService()..throwNextSignIn = const _Renamed();
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-offline')), findsOneWidget);
    });

    testWidgets('treats an http failure as offline too', (tester) async {
      // What a web build throws when the network is gone. Same message
      // discipline: no keyword, so only the type-name branch can see it.
      final service = FakeAccountService()
        ..throwNextSignIn = const ClientException('Failed to fetch');
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-offline')), findsOneWidget);
    });

    testWidgets('a slow sign-in is a failure, not a claim about their wifi',
        (tester) async {
      // Sixty seconds with the Google screen left open is not being
      // offline, and saying so would send somebody to check a connection
      // that is working.
      final hold = Completer<void>();
      final service = _SlowSignIn(hold);
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pump();
      await tester.pump(const Duration(seconds: 61));

      expect(find.byKey(const Key('gate-offline')), findsNothing,
          reason: 'their connection is not the problem');
      expect(find.byKey(const Key('gate-error')), findsOneWidget);

      hold.complete();
      await tester.pumpAndSettle();
    });

    testWidgets('offers no saved work to an anonymous reader, who has none',
        (tester) async {
      // An anonymous installation cannot keep a save - the Firestore
      // rules refuse it - so a button offering to open one would lead to
      // an empty screen, on the one screen whose job is not to be a dead
      // end. It says the useful true thing instead.
      await pumpGate(tester,
          service: FakeAccountService(), onOpenSaved: null);

      expect(find.byKey(const Key('gate-open-saved')), findsNothing);
      expect(find.byKey(const Key('gate-reopen-is-free')), findsOneWidget);
      expect(find.byKey(const Key('gate-back')), findsOneWidget,
          reason: 'still a way out');
    });

    testWidgets('keeps them when there is no account system at all',
        (tester) async {
      // Firebase did not start. Signing in is not on offer and the screen
      // does not pretend otherwise - but the ways out do not need it.
      await pumpGate(tester, service: null);
      expect(find.byKey(const Key('sign-in-with-google')), findsNothing);
      expect(find.byKey(const Key('gate-open-saved')), findsOneWidget);
      expect(find.byKey(const Key('gate-back')), findsOneWidget);
    });

    testWidgets('the ways out are wired to their callbacks', (tester) async {
      var saved = 0, back = 0;
      await pumpGate(
        tester,
        service: FakeAccountService(),
        onOpenSaved: () => saved++,
        onBack: () => back++,
      );
      await tester.tap(find.byKey(const Key('gate-open-saved')));
      await tester.tap(find.byKey(const Key('gate-back')));
      await tester.pump();
      expect(saved, 1);
      expect(back, 1);
    });
  });

  group('signing in', () {
    testWidgets('reports success to the caller, which retries', (tester) async {
      var signedIn = 0;
      await pumpGate(
        tester,
        service: FakeAccountService(),
        onSignedIn: () => signedIn++,
      );

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(signedIn, 1);
      expect(find.byKey(const Key('gate-error')), findsNothing);
    });

    testWidgets('shows a spinner and refuses a second tap while in flight',
        (tester) async {
      final hold = Completer<void>();
      final service = _SlowSignIn(hold);
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pump();

      expect(find.text('Signing in…'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsOneWidget);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pump();
      expect(service.attempts, 1, reason: 'a disabled button cannot be tapped');

      hold.complete();
      await tester.pumpAndSettle();
    });

    testWidgets('changing their mind is not an error', (tester) async {
      final service = FakeAccountService()..failNextSignIn = cancelled;
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-error')), findsNothing);
      expect(find.byKey(const Key('gate-offline')), findsNothing);
    });

    testWidgets('a failure that is not an AccountError still says something',
        (tester) async {
      // The shape of the bug that shipped once: an unexpected exception
      // escaped a handler that caught only AccountError, and the button
      // span for ever.
      final service = FakeAccountService()
        ..throwNextSignIn = UnimplementedError('not on this platform');
      await pumpGate(tester, service: service);

      await tester.tap(find.byKey(const Key('sign-in-with-google')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('gate-error')), findsOneWidget);
      expect(find.text('Signing in…'), findsNothing);
    });
  });
}

/// Minimal stand-in so a sign-in can be held open mid-flight.
class _SlowSignIn extends FakeAccountService {
  _SlowSignIn(this.hold);

  final Completer<void> hold;
  int attempts = 0;

  @override
  Future<void> signIn() async {
    attempts++;
    await hold.future;
    return super.signIn();
  }
}

/// `dart:io` is not available to a web test, and the gate deliberately
/// does not name that type either - it matches on the runtime type NAME,
/// so that one screen can classify the same failure on both platforms.
/// This stands in for it exactly as the real one would.
class SocketException implements Exception {
  const SocketException(this.message);
  final String message;

  @override
  String toString() => 'SocketException: $message';
}

/// What a minified release build hands you: a class whose name tells you
/// nothing, carrying a message that still does.
class _Renamed implements Exception {
  const _Renamed();

  @override
  String toString() => 'SocketException: connection reset';
}

/// package:http's equivalent, which is what a web build actually throws.
class ClientException implements Exception {
  const ClientException(this.message);
  final String message;

  @override
  String toString() => 'ClientException: $message';
}
