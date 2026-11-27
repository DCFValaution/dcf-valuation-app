/// The pill and the card, which must never contradict each other.
///
/// They are two views of ONE `Allowance`: the pill prints `remaining`, and
/// the card appears only when `onLastOne`. So "2 free valuations left"
/// above "One free valuation left" is not a state the app can reach - but
/// it is exactly what a screenshot harness produced by feeding them
/// different numbers, which is why the invariant is written down here
/// rather than left as something that happens to be true.
library;

import 'package:dcf_app/allowance.dart';
import 'package:dcf_app/free_valuations.dart';
import 'package:dcf_app/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Allowance left(int n, {bool degraded = false}) => Allowance(
      plan: 'anonymous',
      window: 'lifetime',
      limit: 3,
      remaining: n,
      degraded: degraded,
    );

Future<void> pump(WidgetTester tester, Widget child) =>
    tester.pumpWidget(MaterialApp(
      theme: buildLightTheme(),
      home: Scaffold(body: Center(child: child)),
    ));

void main() {
  group('the invariant', () {
    test('the card appears only when exactly one is left', () {
      for (var n = 0; n <= 5; n++) {
        expect(left(n).onLastOne, n == 1,
            reason: 'with $n left the card must '
                '${n == 1 ? "appear" : "not appear"}');
      }
    });

    test('so whenever the card is up, the pill reads one', () {
      // The two cannot disagree, because the card's condition IS the
      // pill's number. Stated as a test so that a future change to either
      // has to break this to get through.
      for (var n = 0; n <= 5; n++) {
        final allowance = left(n);
        if (allowance.onLastOne) {
          expect(allowance.remaining, 1);
        }
      }
    });

    test('neither shows when the server admits the count is a guess', () {
      final guessed = left(1, degraded: true);
      expect(guessed.showCount, isFalse);
      expect(guessed.onLastOne, isFalse,
          reason: 'a card that says "one left" on a number we do not '
              'trust is worse than no card');
    });
  });

  group('the pill', () {
    testWidgets('counts one as one', (tester) async {
      // "1 free valuations left" is the kind of thing that makes a reader
      // trust every other number on the screen slightly less.
      await pump(tester, FreeCountPill(allowance: left(1)));
      expect(find.text('1 free valuation left'), findsOneWidget);
    });

    testWidgets('counts more than one as many', (tester) async {
      await pump(tester, FreeCountPill(allowance: left(2)));
      expect(find.text('2 free valuations left'), findsOneWidget);
    });

    testWidgets('counts none as none, in the plural', (tester) async {
      await pump(tester, FreeCountPill(allowance: left(0)));
      expect(find.text('0 free valuations left'), findsOneWidget);
    });
  });

  group('the card', () {
    testWidgets('says one, and offers the way out', (tester) async {
      var tapped = 0;
      await pump(tester, LastFreeValuationCard(onSignIn: () => tapped++));

      expect(find.text('One free valuation left'), findsOneWidget);
      await tester.tap(find.byKey(const Key('last-free-sign-in')));
      expect(tapped, 1);
    });

    testWidgets('offers nothing when there is no account system',
        (tester) async {
      await pump(tester, const LastFreeValuationCard());
      expect(find.byKey(const Key('last-free-sign-in')), findsNothing);
      expect(find.text('One free valuation left'), findsOneWidget,
          reason: 'it still says what is happening');
    });
  });
}
