// The sensitivity table every honesty note points to.
//
// Its one hard promise is that the outlined centre cell is the headline
// figure - otherwise the table contradicts the number it is meant to qualify.
// The DCF and DDM centre cells differ from the headline by about 2e-8 (the
// backend rounds the discount-rate axis to ten decimals before recomputing),
// so the match is asserted where a reader can see it: at the cent, through
// the same formatter the headline uses. The speculative centre is exact.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/sensitivity_table.dart';
import 'package:dcf_app/speculative_screen.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

Map<String, dynamic> methodCase(String ticker) =>
    (jsonDecode(File('test/fixtures/method_cases.json').readAsStringSync())
                as List<dynamic>)
            .cast<Map<String, dynamic>>()
            .firstWhere((c) => c['ticker'] == ticker)['body']
        as Map<String, dynamic>;

Map<String, dynamic> speculativeCase(String name) =>
    (jsonDecode(File('test/fixtures/speculative_cases.json').readAsStringSync())
            as Map<String, dynamic>)[name]['body']
        as Map<String, dynamic>;

Map<String, dynamic> edgeCase(String name) =>
    (jsonDecode(File('test/fixtures/sensitivity_cases.json').readAsStringSync())
            as Map<String, dynamic>)[name]
        as Map<String, dynamic>;

String dollars(double v) => '\$${v.toStringAsFixed(2)}';
String signedDollars(double v) =>
    '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';

String centreText(WidgetTester tester) => tester
    .widget<Text>(
      find.descendant(
        of: find.byKey(const Key('sensitivity-centre')),
        matching: find.byType(Text),
      ),
    )
    .data!;

ValuationApi backendFor(Map<String, Map<String, dynamic>> byPath) =>
    ValuationApi(
      baseUrl: 'http://test',
      client: MockClient((request) async {
        final body = byPath[request.url.path];
        if (body != null) return http.Response(jsonEncode(body), 200);
        if (request.url.path == '/search') {
          return http.Response('{"query":"","results":[]}', 200);
        }
        return http.Response('{"status":"ok"}', 200);
      }),
    );

Future<void> value(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

void main() {
  group('the centre cell is the headline', () {
    test('DCF (AAPL): same figure to the cent', () {
      final result = ValuationSuccess.fromJson(methodCase('AAPL'));
      final grid = result.sensitivity!;

      expect(grid.rowAxis, 'Discount rate (WACC)');
      expect(grid.columnAxis, 'Terminal growth');
      expect(dollars(grid.centreValue), dollars(result.intrinsicValuePerShare));
      expect(
        (grid.centreValue - result.intrinsicValuePerShare).abs(),
        lessThan(1e-6),
      );
    });

    test('DDM (JPM): same figure to the cent, on its own axes', () {
      final result = ValuationSuccess.fromJson(methodCase('JPM'));
      final grid = result.sensitivity!;

      expect(result.method, ValuationMethod.ddm);
      expect(grid.rowAxis, 'Cost of equity');
      expect(dollars(grid.centreValue), dollars(result.intrinsicValuePerShare));
      expect(
        (grid.centreValue - result.intrinsicValuePerShare).abs(),
        lessThan(1e-6),
      );
    });

    test('speculative (RIVN): exactly the estimate', () {
      final estimate = SpeculativeEstimate.fromJson(
        speculativeCase('rivn_speculative'),
      );
      final grid = estimate.sensitivity!;

      expect(grid.rowAxis, 'Target operating margin');
      expect(grid.columnAxis, 'Years to profitability');
      expect(grid.columnsArePercent, isFalse);
      expect(grid.centreValue, estimate.valuePerShare);
    });

    testWidgets('on screen, AAPL\'s outlined cell reads as its headline', (
      tester,
    ) async {
      final body = methodCase('AAPL');
      await tester.pumpWidget(
        DcfApp(api: backendFor({'/valuation/AAPL': body})),
      );
      await value(tester, 'AAPL');

      final headline = dollars(
        (body['intrinsic_value_per_share'] as num).toDouble(),
      );
      expect(find.text(headline), findsWidgets);
      expect(centreText(tester), headline);
      expect(find.text('How sensitive is this figure?'), findsOneWidget);
    });

    testWidgets('on screen, JPM\'s outlined cell reads as its headline', (
      tester,
    ) async {
      final body = methodCase('JPM');
      await tester.pumpWidget(
        DcfApp(api: backendFor({'/valuation/JPM': body})),
      );
      await value(tester, 'JPM');

      expect(
        centreText(tester),
        dollars((body['intrinsic_value_per_share'] as num).toDouble()),
      );
      expect(find.text('↓ Cost of equity'), findsOneWidget);
    });

    testWidgets(
      'on the speculative screen, RIVN\'s outlined cell is the estimate',
      (tester) async {
        final body = speculativeCase('rivn_speculative');
        await tester.pumpWidget(
          MaterialApp(
            theme: buildLightTheme(),
            home: SpeculativeScreen(
              api: backendFor({'/valuation/RIVN/speculative': body}),
              ticker: 'RIVN',
              companyName: 'Rivian Automotive, Inc.',
            ),
          ),
        );
        await tester.pumpAndSettle();

        final estimate = (body['speculative_value_per_share'] as num)
            .toDouble();
        final figure = tester
            .widget<Text>(find.byKey(const Key('speculative-figure')))
            .data;
        expect(centreText(tester), signedDollars(estimate));
        expect(centreText(tester), figure);
        expect(find.text('How far this estimate swings'), findsOneWidget);
        expect(
          find.textContaining('changing sign along the way'),
          findsOneWidget,
          reason: 'RIVN\'s table runs from negative to positive',
        );
      },
    );
  });

  group('blank cells', () {
    test('are kept as the backend\'s nulls, never turned into numbers', () {
      final body = edgeCase('aapl_growth_near_wacc');
      final raw = (body['sensitivity']['grid'] as List)
          .map((r) => (r as List).map((c) => c == null).toList())
          .toList();
      final grid = ValuationSuccess.fromJson(body).sensitivity!;

      for (var r = 0; r < raw.length; r++) {
        for (var c = 0; c < raw[r].length; c++) {
          expect(grid.cells[r][c] == null, raw[r][c], reason: 'cell $r,$c');
        }
      }
      expect(grid.hasBlanks, isTrue);
    });

    testWidgets('render as dashes, with the reason given once', (tester) async {
      final body = edgeCase('aapl_growth_near_wacc');
      final grid = ValuationSuccess.fromJson(body).sensitivity!;
      final blanks = grid.cells.expand((r) => r).where((c) => c == null).length;

      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: Scaffold(
            body: SingleChildScrollView(
              child: SensitivityTable(
                grid: grid,
                formatValue: dollars,
                title: 't',
                explanation: 'e',
              ),
            ),
          ),
        ),
      );

      expect(find.text('—'), findsNWidgets(blanks));
      expect(
        find.textContaining('terminal growth at or above the discount rate'),
        findsOneWidget,
      );
      expect(find.textContaining('NaN'), findsNothing);
      expect(find.textContaining('Infinity'), findsNothing);
    });

    test(
      'the speculative blank row is explained as no path to profitability',
      () {
        final grid = SpeculativeEstimate.fromJson(
          edgeCase('rivn_zero_margin_row'),
        ).sensitivity!;

        expect(grid.cells.first.every((c) => c == null), isTrue);
        expect(grid.blankMeaning, contains('no path to profitability'));
      },
    );

    test('the range ignores blanks', () {
      final grid = ValuationSuccess.fromJson(edgeCase('aapl_growth_near_wacc'))
          .sensitivity!;
      expect(grid.values.every((v) => v.isFinite), isTrue);
      expect(
        grid.values.length,
        grid.cells.expand((r) => r).where((c) => c != null).length,
      );
    });
  });

  group('no table where there is no meaningful one', () {
    Map<String, dynamic> withSensitivity(
      Map<String, dynamic> Function(Map<String, dynamic>) edit,
    ) {
      final body =
          jsonDecode(jsonEncode(methodCase('AAPL'))) as Map<String, dynamic>;
      body['sensitivity'] = edit(body['sensitivity'] as Map<String, dynamic>);
      return body;
    }

    test('when the backend sends none', () {
      final body = Map<String, dynamic>.of(methodCase('AAPL'))
        ..remove('sensitivity');
      expect(ValuationSuccess.fromJson(body).sensitivity, isNull);
    });

    test('when the centre cell - the headline\'s own case - is blank', () {
      final body = withSensitivity((s) {
        (s['grid'] as List)[s['centre_row']][s['centre_col']] = null;
        return s;
      });
      expect(ValuationSuccess.fromJson(body).sensitivity, isNull);
    });

    test('when the centre lies outside the grid', () {
      final body = withSensitivity((s) => s..['centre_row'] = 9);
      expect(ValuationSuccess.fromJson(body).sensitivity, isNull);
    });

    test('when rows and axes disagree in size', () {
      final body = withSensitivity((s) {
        ((s['grid'] as List).first as List).removeLast();
        return s;
      });
      expect(ValuationSuccess.fromJson(body).sensitivity, isNull);
    });

    testWidgets('and then nothing is drawn', (tester) async {
      final body = Map<String, dynamic>.of(methodCase('AAPL'))
        ..remove('sensitivity');
      await tester.pumpWidget(
        DcfApp(api: backendFor({'/valuation/AAPL': body})),
      );
      await value(tester, 'AAPL');

      expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
      expect(find.byType(SensitivityTable), findsNothing);
    });
  });

  testWidgets('while a slider moves the headline, the table says it is behind', (
    tester,
  ) async {
    final body = methodCase('AAPL');
    await tester.pumpWidget(DcfApp(api: backendFor({'/valuation/AAPL': body})));
    await value(tester, 'AAPL');
    expect(find.byKey(const Key('sensitivity-range')), findsOneWidget);

    // A local preview: the headline now describes assumptions the table does not.
    tester
        .widget<AssumptionSliders>(find.byType(AssumptionSliders))
        .onChanged('wacc', 0.15);
    await tester.pump();

    expect(find.textContaining('Out of date'), findsOneWidget);
    expect(
      find.byKey(const Key('sensitivity-range')),
      findsNothing,
      reason:
          'the range must not be quoted beside a figure it no longer describes',
    );
  });
}
