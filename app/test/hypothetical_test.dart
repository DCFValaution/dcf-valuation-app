// The user-built hypothetical: the last opt-in, and the weakest claim here.
//
// Most of what matters is what must NOT happen. No hypothetical without two
// deliberate taps; no figure at all until the user has assumed a profit;
// nothing pre-filled that flatters the company; and the company's own figures
// always beside the assumption that departs from them.
//
// Every response is the real backend's, recorded from Intel - a company that
// loses money AND is shrinking - by tools/generate_hypothetical_fixtures.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/hypothetical_screen.dart';
import 'package:dcf_app/speculative_screen.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/hypothetical_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> body(String name) =>
    _cases[name]['body'] as Map<String, dynamic>;

int statusOf(String name) => _cases[name]['status_code'] as int;

/// A backend answering from the recordings, remembering every request.
class Backend {
  final List<http.Request> requests = [];

  List<String> get paths => requests.map((r) => r.url.path).toList();
  bool get askedForHypothetical =>
      paths.any((p) => p.endsWith('/hypothetical'));

  /// The query the app sent for the most recent hypothetical.
  Map<String, String> get lastHypotheticalQuery => requests
      .lastWhere((r) => r.url.path.endsWith('/hypothetical'))
      .url
      .queryParameters;

  ValuationApi api({String assumed = 'intc_hypothetical'}) => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      final path = request.url.path;

      if (path.endsWith('/hypothetical')) {
        // The neutral placeholders are answered as the backend answers them:
        // no figure, because no profit has been assumed yet.
        final margin =
            double.tryParse(
              request.url.queryParameters['target_operating_margin'] ?? '0',
            ) ??
            0;
        final key = margin <= 0 ? 'intc_neutral' : assumed;
        return http.Response(
          jsonEncode(body(key)),
          statusOf(key),
          headers: const {'content-type': 'application/json'},
        );
      }
      if (path.endsWith('/speculative')) {
        return http.Response(
          jsonEncode(body('intc_speculative_refused')),
          statusOf('intc_speculative_refused'),
          headers: const {'content-type': 'application/json'},
        );
      }
      return http.Response('{"status":"ok","query":"","results":[]}', 200);
    }),
  );
}

HypotheticalInputs get _placeholders {
  final p =
      body('intc_speculative_refused')['hypothetical_placeholders']
          as Map<String, dynamic>;
  return HypotheticalInputs(
    revenueGrowth: (p['revenue_growth'] as num).toDouble(),
    targetOperatingMargin: (p['target_operating_margin'] as num).toDouble(),
    yearsToTarget: (p['years_to_target'] as num).round(),
  );
}

Future<Backend> pumpScreen(
  WidgetTester tester, {
  String assumed = 'intc_hypothetical',
}) async {
  final backend = Backend();
  await tester.pumpWidget(
    MaterialApp(
      theme: buildLightTheme(),
      home: HypotheticalScreen(
        api: backend.api(assumed: assumed),
        ticker: 'INTC',
        companyName: 'Intel Corporation',
        placeholders: _placeholders,
      ),
    ),
  );
  await tester.pump();
  await tester.pump();
  return backend;
}

void main() {
  group('the recorded backend itself', () {
    test('refuses Intel a speculative estimate, and offers this instead', () {
      final refusal = body('intc_speculative_refused');

      expect(statusOf('intc_speculative_refused'), 422);
      expect(refusal['hypothetical_available'], isTrue);
      expect(
        (refusal['reasons'] as List<dynamic>).join(' '),
        contains('shrinking company'),
      );
    });

    test('offers only neutral placeholders - no growth, no profit', () {
      final p =
          body('intc_speculative_refused')['hypothetical_placeholders']
              as Map<String, dynamic>;

      expect(p['revenue_growth'], 0);
      expect(p['target_operating_margin'], 0);
    });

    test('never calls the result a valuation', () {
      final h = body('intc_hypothetical');

      expect(h['method'], 'hypothetical');
      expect(h['is_valuation'], isFalse);
      expect(h['derived_from_company_data'], isFalse);
      expect(h.containsKey('intrinsic_value_per_share'), isFalse);
      expect(h.containsKey('speculative_value_per_share'), isFalse);
      // No upside, no downside, no percentage against the price.
      expect(h.containsKey('estimate_vs_price'), isFalse);
      expect(h.containsKey('upside_downside'), isFalse);
    });

    test('carries Intel\'s real trajectory beside the assumption', () {
      final reality = (body('intc_hypothetical')['reality'] as List<dynamic>)
          .cast<Map<String, dynamic>>();
      final growth = reality.firstWhere((c) => c['name'] == 'revenue_growth');

      expect(growth['contradicts'], isTrue);
      expect(growth['actual'], lessThan(0));
      expect(growth['statement'], contains('actually fallen'));
    });
  });

  group('reaching it', () {
    testWidgets('the refusal is still the answer, and offers the opt-in', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: SpeculativeScreen(
            api: backend.api(),
            ticker: 'INTC',
            companyName: 'Intel Corporation',
          ),
        ),
      );
      await tester.pump();
      await tester.pump();

      // The honest refusal is the primary content.
      expect(find.text('No speculative estimate'), findsOneWidget);
      expect(find.byKey(const Key('hypothetical-opt-in')), findsOneWidget);
      // And nothing has been computed on the user's behalf.
      expect(backend.askedForHypothetical, isFalse);
    });

    testWidgets('the opt-in says what it is before it is tapped', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: SpeculativeScreen(
            api: backend.api(),
            ticker: 'INTC',
            companyName: 'Intel Corporation',
          ),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.textContaining('It is not a valuation'), findsWidgets);
      expect(
        find.textContaining('nothing about it is derived from the data'),
        findsOneWidget,
      );
    });
  });

  group('the screen', () {
    testWidgets('opens with no figure, because no profit is assumed yet', (
      tester,
    ) async {
      await pumpScreen(tester);

      expect(find.byKey(const Key('hypothetical-incomplete')), findsOneWidget);
      expect(find.byKey(const Key('hypothetical-value')), findsNothing);
      expect(find.text('No figure yet'), findsOneWidget);
    });

    testWidgets('says the choice is the user\'s, not the app\'s', (
      tester,
    ) async {
      await pumpScreen(tester);

      expect(
        find.textContaining('nothing here will pick one for you'),
        findsOneWidget,
      );
    });

    testWidgets('sends exactly the assumptions the user set', (tester) async {
      final backend = await pumpScreen(tester);
      final query = backend.lastHypotheticalQuery;

      expect(query['revenue_growth'], '0.0');
      expect(query['target_operating_margin'], '0.0');
      expect(query['years_to_target'], '5');
    });

    testWidgets('shows the disclaimer before anything else', (tester) async {
      await pumpScreen(tester);

      final disclaimer = find.byKey(const Key('hypothetical-disclaimer'));
      expect(disclaimer, findsOneWidget);
      expect(
        find.textContaining('You are building this, not the app'),
        findsOneWidget,
      );
      expect(
        find.textContaining('It is not a valuation and not a price target'),
        findsOneWidget,
      );
      // Above the sliders it argues with.
      expect(
        tester.getTopLeft(disclaimer).dy,
        lessThan(tester.getTopLeft(find.text('Assumptions')).dy),
      );
    });
  });

  group('with a hypothetical built', () {
    /// Drives the target-margin slider up, which is what turns the neutral
    /// state into a claim.
    Future<Backend> withAProfitAssumed(WidgetTester tester) async {
      final backend = await pumpScreen(tester);
      final slider = find.byType(Slider).at(1);
      await tester.ensureVisible(slider);
      await tester.pump();
      await tester.drag(slider, const Offset(200, 0));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pump();
      return backend;
    }

    testWidgets('the figure is labelled as the user\'s, never as a value', (
      tester,
    ) async {
      await withAProfitAssumed(tester);

      expect(find.byKey(const Key('hypothetical-value')), findsOneWidget);
      expect(find.text('WHAT YOUR ASSUMPTIONS IMPLY'), findsOneWidget);
      expect(find.text('NOT A VALUATION'), findsOneWidget);
      // None of the app's valuation language appears anywhere on it.
      expect(find.textContaining('INTRINSIC VALUE'), findsNothing);
      expect(find.textContaining('SPECULATIVE ESTIMATE'), findsNothing);
      expect(find.textContaining('IMPLIED UPSIDE'), findsNothing);
      expect(find.textContaining('IMPLIED DOWNSIDE'), findsNothing);
    });

    testWidgets('no upside or downside is drawn against the price', (
      tester,
    ) async {
      await withAProfitAssumed(tester);

      expect(
        find.textContaining('no comparison is drawn between the two'),
        findsOneWidget,
      );
    });

    testWidgets('Intel\'s real figures sit with the assumptions', (
      tester,
    ) async {
      await withAProfitAssumed(tester);

      expect(
        find.byKey(const ValueKey('reality-revenue_growth')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('reality-target_operating_margin')),
        findsOneWidget,
      );
      expect(find.textContaining('has actually fallen'), findsOneWidget);
    });

    testWidgets('the contradiction sits under the slider it contradicts', (
      tester,
    ) async {
      await withAProfitAssumed(tester);

      final note = find.byKey(const ValueKey('reality-revenue_growth'));
      expect(
        tester.getTopLeft(note).dy,
        greaterThan(tester.getTopLeft(find.byType(Slider).first).dy),
      );
    });
  });
}
