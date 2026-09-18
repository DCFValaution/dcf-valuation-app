// "This model may not fit this company" - pinned beside the figure.
//
// Most warnings are caveats about an assumption: growth capped, a tax credit
// ignored. A reader can weigh those against the figure, and they live in the
// "Keep in mind" card further down. A few say something stronger - that the
// method itself may not describe the company - and those qualify the implied
// upside or downside too. Robinhood is the case: a DCF produced $16 against a
// $104 price, and the reason that gap is probably a model-fit artefact sat two
// screens below it as the first of four ordinary bullets.
//
// These tests hold the new arrangement: the doubt is on screen with the
// figure, visibly distinct, shown once; and an ordinary company is untouched.
//
// Responses are the real backend's, recorded by
// tools/generate_method_fit_fixtures.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/method_fit_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> body(String name) =>
    _cases[name]['body'] as Map<String, dynamic>;

const _heading = 'This model may not fit this company';
final _pinned = find.byKey(const Key('method-fit-warning'));

Future<void> valueOnScreen(WidgetTester tester, String name) async {
  final ticker =
      (body(name)['company'] as Map<String, dynamic>)['ticker'] as String;
  await tester.pumpWidget(
    DcfApp(
      api: ValuationApi(
        baseUrl: 'http://test',
        client: MockClient(
          (r) async => r.url.path == '/valuation/$ticker'
              ? http.Response(
                  jsonEncode(body(name)),
                  200,
                  headers: const {'content-type': 'application/json'},
                )
              : http.Response('{"status":"ok","query":"","results":[]}', 200),
        ),
      ),
    ),
  );
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

void main() {
  group('the recorded backend', () {
    test('sends Robinhood\'s doubt in its own field, once', () {
      final hood = body('hood');
      final methodFit = (hood['method_fit_warnings'] as List<dynamic>)
          .cast<String>();
      final ordinary = (hood['warnings'] as List<dynamic>).cast<String>();

      expect(methodFit, hasLength(1));
      expect(methodFit.single, contains('lending activity'));
      expect(ordinary.any((w) => w.contains('lending activity')), isFalse);
    });

    test('sends Apple none', () {
      expect(body('aapl')['method_fit_warnings'], isEmpty);
    });
  });

  group('Robinhood', () {
    testWidgets('shows the doubt, under its own heading', (tester) async {
      await valueOnScreen(tester, 'hood');

      expect(_pinned, findsOneWidget);
      expect(
        find.descendant(of: _pinned, matching: find.text(_heading)),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: _pinned,
          matching: find.textContaining('lending activity'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('pins it directly under the figure, with the downside', (
      tester,
    ) async {
      await valueOnScreen(tester, 'hood');

      final figure = tester.getRect(
        find
            .ancestor(
              of: find.text('INTRINSIC VALUE PER SHARE'),
              matching: find.byType(Container),
            )
            .first,
      );
      final pinned = tester.getRect(_pinned);
      final downside = tester.getRect(find.text('IMPLIED DOWNSIDE'));

      // Below the figure and its implied downside...
      expect(pinned.top, greaterThan(figure.top));
      expect(pinned.top, greaterThan(downside.bottom));
      // ...and on the first screen with them: a phone is roughly 800 logical
      // pixels tall, and the old placement was two screens further down.
      expect(pinned.top, lessThan(downside.bottom + 200));
    });

    testWidgets('puts it above the note and the sensitivity table', (
      tester,
    ) async {
      await valueOnScreen(tester, 'hood');

      final pinned = tester.getTopLeft(_pinned).dy;
      final note = tester
          .getTopLeft(find.textContaining('is the output of these assumptions'))
          .dy;

      expect(pinned, lessThan(note));

      await tester.ensureVisible(find.byKey(const Key('sensitivity-table')));
      await tester.pump();
      // Still above it once scrolled - the order does not depend on the
      // viewport.
      expect(
        tester.getTopLeft(_pinned).dy,
        lessThan(
          tester.getTopLeft(find.byKey(const Key('sensitivity-table'))).dy,
        ),
      );
    });

    testWidgets('shows it once - not repeated among the ordinary caveats', (
      tester,
    ) async {
      await valueOnScreen(tester, 'hood');

      expect(find.textContaining('lending activity'), findsOneWidget);
    });

    testWidgets('keeps the ordinary caveats where they were', (tester) async {
      await valueOnScreen(tester, 'hood');
      await tester.ensureVisible(find.text('Keep in mind'));
      await tester.pump();

      expect(find.text('Keep in mind'), findsOneWidget);
      // Growth capped, a tax credit ignored: assumption notes, still listed.
      expect(find.textContaining('revenue grew'), findsOneWidget);
    });
  });

  group('Apple', () {
    testWidgets('has no pinned doubt - its screen is unchanged', (
      tester,
    ) async {
      await valueOnScreen(tester, 'aapl');

      expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
      expect(_pinned, findsNothing);
      expect(find.text(_heading), findsNothing);
    });
  });
}
