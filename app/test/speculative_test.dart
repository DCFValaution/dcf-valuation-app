// The opt-in speculative flow.
//
// What has to hold is mostly about what must NOT happen: no speculative
// request without a tap, no opt-in on a refusal the backend did not mark as
// eligible, no speculative figure dressed as a valuation. Responses are the
// real backend's, recorded by tools/generate_speculative_fixtures.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/speculative_screen.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/speculative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> body(String name) =>
    _cases[name]['body'] as Map<String, dynamic>;

int statusOf(String name) => _cases[name]['status_code'] as int;

Map<String, dynamic> aaplValuation() =>
    (jsonDecode(File('test/fixtures/method_cases.json').readAsStringSync())
                as List<dynamic>)
            .cast<Map<String, dynamic>>()
            .firstWhere((c) => c['ticker'] == 'AAPL')['body']
        as Map<String, dynamic>;

String money(double v) => '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';

/// A backend that answers from the recordings and remembers every request.
class Backend {
  final List<http.Request> requests = [];

  List<String> get paths => requests.map((r) => r.url.path).toList();
  bool get askedForSpeculation => paths.any((p) => p.contains('speculative'));

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      http.Response recorded(String name) =>
          http.Response(jsonEncode(body(name)), statusOf(name));

      switch (request.url.path) {
        case '/valuation/RIVN':
          return recorded('rivn_refusal');
        case '/valuation/QS':
          return recorded('qs_refusal');
        case '/valuation/AXP':
          return recorded('axp_refusal');
        case '/valuation/AAPL':
          return http.Response(jsonEncode(aaplValuation()), 200);
        case '/valuation/RIVN/speculative':
          return recorded('rivn_speculative');
        case '/valuation/QS/speculative':
          return recorded('qs_speculative_refused');
        case '/valuation/speculative':
          return recorded('rivn_speculative_overridden');
        case '/search':
          return http.Response('{"query":"","results":[]}', 200);
      }
      return http.Response('{"status":"ok"}', 200);
    }),
  );
}

Future<void> valueTicker(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

final _optIn = find.byKey(const Key('speculative-opt-in'));

Future<void> openSpeculative(WidgetTester tester) async {
  await tester.ensureVisible(_optIn);
  await tester.tap(_optIn);
  await tester.pumpAndSettle();
}

void main() {
  group('the refusal', () {
    testWidgets('an eligible loss-maker is refused by default, with the opt-in '
        'offered and nothing speculative fetched', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));

      await valueTicker(tester, 'RIVN');

      expect(
        find.text('A standard DCF isn’t the right tool here'),
        findsOneWidget,
      );
      expect(find.textContaining('loss-making'), findsWidgets);
      expect(_optIn, findsOneWidget);
      expect(find.text('Show a speculative estimate anyway'), findsOneWidget);
      expect(
        backend.askedForSpeculation,
        isFalse,
        reason: 'offering it must not fetch it',
      );
      expect(find.byType(SpeculativeScreen), findsNothing);
    });

    for (final (ticker, why) in [
      ('QS', 'a no-revenue company, which the speculative path refuses too'),
      ('AXP', 'a lender, refused for more than losing money'),
    ]) {
      testWidgets('$ticker gets no opt-in: $why', (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));

        await valueTicker(tester, ticker);

        expect(
          find.text('A standard DCF isn’t the right tool here'),
          findsOneWidget,
        );
        expect(_optIn, findsNothing);
        expect(find.textContaining('speculative'), findsNothing);
        expect(backend.askedForSpeculation, isFalse);
      });
    }

    testWidgets('a normal valuation is untouched: no opt-in anywhere', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));

      await valueTicker(tester, 'AAPL');

      expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
      expect(_optIn, findsNothing);
      expect(find.textContaining('speculative'), findsNothing);
      expect(backend.askedForSpeculation, isFalse);
    });
  });

  group('the estimate', () {
    testWidgets('is only fetched once the opt-in is tapped', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      expect(backend.askedForSpeculation, isFalse);

      await openSpeculative(tester);

      expect(backend.paths, contains('/valuation/RIVN/speculative'));
      expect(find.byType(SpeculativeScreen), findsOneWidget);
    });

    testWidgets('carries the full disclaimer, first, under a warning header', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      final disclaimer = find.byKey(const Key('speculative-disclaimer'));
      expect(disclaimer, findsOneWidget);
      expect(
        find.descendant(
          of: disclaimer,
          matching: find.byIcon(Icons.warning_rounded),
        ),
        findsOneWidget,
      );
      expect(find.textContaining('SPECULATIVE ESTIMATE'), findsWidgets);

      // In full: the opening and the closing clauses are both there.
      final full = body('rivn_speculative')['disclaimer'] as String;
      expect(
        find.descendant(
          of: disclaimer,
          matching: find.textContaining('must not be treated as one'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: disclaimer,
          matching: find.textContaining('reason to buy or sell'),
        ),
        findsOneWidget,
      );
      expect(full, contains('reason to buy or sell'), reason: 'fixture sanity');

      // Above the figure, so it is read first.
      final figure = find.byKey(const Key('speculative-figure'));
      expect(
        tester.getTopLeft(disclaimer).dy,
        lessThan(tester.getTopLeft(figure).dy),
      );
    });

    testWidgets('is never labelled intrinsic value, and has no upside', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      expect(find.text('SPECULATIVE ESTIMATE PER SHARE'), findsOneWidget);
      expect(find.text('NOT A VALUATION'), findsOneWidget);
      expect(find.textContaining('INTRINSIC VALUE'), findsNothing);
      expect(find.textContaining('UPSIDE'), findsNothing);
      expect(find.textContaining('DOWNSIDE'), findsNothing);
    });

    testWidgets('shows a negative estimate as negative, and explains it', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      final value =
          (body('rivn_speculative')['speculative_value_per_share'] as num)
              .toDouble();
      expect(value, isNegative, reason: 'RIVN comes out negative');
      expect(
        tester.widget<Text>(find.byKey(const Key('speculative-figure'))).data,
        money(value),
      );
      expect(
        find.textContaining('Negative: even on these assumptions'),
        findsOneWidget,
      );
      expect(
        find.textContaining('lose at most what they paid'),
        findsOneWidget,
      );
    });

    testWidgets('carries the cash-burn and dilution warnings', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      final warnings = (body('rivn_speculative')['warnings'] as List)
          .cast<String>();
      expect(warnings, isNotEmpty);
      expect(find.textContaining('burns \$'), findsOneWidget);
      expect(find.textContaining('dilute existing holders'), findsOneWidget);
    });

    testWidgets('puts every path assumption on a slider, with its source', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      final sliders = tester.widget<AssumptionSliders>(
        find.byType(AssumptionSliders),
      );
      expect(sliders.specs, same(kSpeculativeAdjustable));
      for (final spec in kSpeculativeAdjustable) {
        expect(find.text(spec.label), findsOneWidget);
        expect(sliders.sources[spec.name], isNotEmpty, reason: spec.name);
      }
      expect(sliders.sources['years_to_profitability'], 'derived');
      expect(sliders.sources['target_operating_margin'], 'default');
      expect(find.text('6 years'), findsOneWidget);
      expect(
        find.textContaining('valuation updates'),
        findsNothing,
        reason: 'the slider panel must not call this a valuation',
      );
    });

    testWidgets('moving a slider re-asks the backend with the override', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'RIVN');
      await openSpeculative(tester);

      final sliders = tester.widget<AssumptionSliders>(
        find.byType(AssumptionSliders),
      );
      sliders.onChanged('years_to_profitability', 4);
      sliders.onChanged('target_operating_margin', 0.25);
      await tester.pump();
      expect(find.textContaining('Figure not yet updated'), findsOneWidget);

      sliders.onChangeEnd();
      await tester.pump(const Duration(milliseconds: 350));
      await tester.pump();

      final post = backend.requests.lastWhere(
        (r) => r.url.path == '/valuation/speculative',
      );
      final sent = jsonDecode(post.body) as Map<String, dynamic>;
      expect(sent['ticker'], 'RIVN');
      expect(sent['overrides']['years_to_profitability'], 4);
      expect(
        sent['overrides']['years_to_profitability'],
        isA<int>(),
        reason: 'the backend accepts years only as a whole number',
      );
      expect(sent['overrides']['target_operating_margin'], 0.25);

      final updated =
          (body('rivn_speculative_overridden')['speculative_value_per_share']
                  as num)
              .toDouble();
      expect(
        tester.widget<Text>(find.byKey(const Key('speculative-figure'))).data,
        money(updated),
      );
      expect(
        find.textContaining('Figure matches these assumptions'),
        findsOneWidget,
      );
    });

    testWidgets('where even a path to profitability does not apply, says so', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: SpeculativeScreen(
            api: backend.api(),
            ticker: 'QS',
            companyName: 'QuantumScape',
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('No speculative estimate'), findsOneWidget);
      expect(find.byKey(const Key('speculative-figure')), findsNothing);
    });
  });

  group('getting back', () {
    for (final (label, how) in [
      ('the app bar', find.byTooltip('Back to the refusal')),
      (
        'the button at the end',
        find.widgetWithText(TextButton, 'Back to the refusal'),
      ),
    ]) {
      testWidgets('$label returns to the plain refusal, as it was', (
        tester,
      ) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await valueTicker(tester, 'RIVN');
        await openSpeculative(tester);

        await tester.ensureVisible(how);
        await tester.tap(how);
        await tester.pumpAndSettle();

        expect(find.byType(SpeculativeScreen), findsNothing);
        expect(
          find.text('A standard DCF isn’t the right tool here'),
          findsOneWidget,
        );
        expect(_optIn, findsOneWidget);
        expect(find.textContaining('SPECULATIVE ESTIMATE'), findsNothing);
      });
    }
  });

  group('SpeculativeEstimate', () {
    test('reads the recorded response', () {
      final e = SpeculativeEstimate.fromJson(body('rivn_speculative'));

      expect(e.ticker, 'RIVN');
      expect(e.isNegative, isTrue);
      expect(e.headline, contains('NOT A VALUATION'));
      expect(e.disclaimer, contains('not a valuation'));
      expect(e.assumption('years_to_profitability')!.source, 'derived');
      expect(e.warnings, isNotEmpty);
    });

    test('a 422 is a refusal with its reasons, not a failure', () async {
      final outcome = await Backend().api().speculative('QS');

      expect(outcome, isA<SpeculativeRefused>());
      expect(
        (outcome as SpeculativeRefused).message,
        contains('No speculative estimate'),
      );
    });
  });
}
