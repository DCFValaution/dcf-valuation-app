/// A valuation with no share price.
///
/// The price is not an input to any figure the model produces - only to the
/// comparison with it - so when the price source will not give us one the
/// valuation is still shown and the comparison is dropped. The risk here is
/// cosmetic and therefore real: a missing percentage that renders as
/// "+0.0%", or a green badge on nothing, would read as a measurement.
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

Map<String, dynamic> response({
  num? price,
  num? upside,
  String asOfText = 'close as of 20 November 2026',
}) => {
  'method': 'dcf',
  'company': {
    'ticker': 'AAPL',
    'company_name': 'Apple Inc.',
    'sector': 'Technology',
    'exchange': 'NasdaqGS',
    'price_as_of': price == null ? null : '2026-11-20',
    'price_as_of_text': asOfText,
  },
  'suitable': true,
  'intrinsic_value_per_share': 204.0,
  'current_price': price,
  'upside_downside': upside,
  'note': 'A caveat that is displayed verbatim.',
  'assumptions': <dynamic>[],
  'wacc_inputs': <dynamic>[],
  'warnings': <dynamic>[],
  'warning_items': <dynamic>[],
};

ValuationApi apiServing(Map<String, dynamic> body) => ValuationApi(
  baseUrl: 'http://test',
  client: MockClient((request) async {
    if (request.url.path == '/health') {
      return http.Response(jsonEncode({'status': 'ok'}), 200);
    }
    if (request.url.path == '/search') {
      return http.Response(
        jsonEncode({
          'status': 'ok',
          'results': [
            {
              'ticker': 'AAPL',
              'name': 'Apple Inc.',
              'exchange': 'NasdaqGS',
              'region': 'US',
            },
          ],
        }),
        200,
      );
    }
    return http.Response(jsonEncode(body), 200);
  }),
);

Future<void> value(WidgetTester tester, Map<String, dynamic> body) async {
  await tester.pumpWidget(DcfApp(api: apiServing(body)));
  await tester.pumpAndSettle();
  await tester.enterText(find.byType(TextField).first, 'AAPL');
  await tester.pumpAndSettle();
  await tester.testTextInput.receiveAction(TextInputAction.search);
  await tester.pumpAndSettle();
}

void main() {
  group('parsing', () {
    test('a priced response reads the price and the comparison', () {
      final r = ValuationSuccess.fromJson(
        response(price: 349.21, upside: -0.416),
      );
      expect(r.currentPrice, 349.21);
      expect(r.upsideDownside, closeTo(-0.416, 1e-9));
      expect(r.isUndervalued, isFalse);
    });

    test('an unpriced response parses, with both fields null', () {
      final r = ValuationSuccess.fromJson(
        response(asOfText: 'no price available'),
      );
      expect(r.currentPrice, isNull);
      expect(r.upsideDownside, isNull);
      expect(r.intrinsicValuePerShare, 204.0, reason: 'the valuation stands');
    });

    test('a null comparison is not read as undervalued', () {
      // The trap: `upsideDownside > 0` on a null throws, and
      // `(upsideDownside ?? 0) > 0` must come out false rather than true.
      expect(ValuationSuccess.fromJson(response()).isUndervalued, isFalse);
    });

    test('the price date is carried even when there is no price', () {
      final r = ValuationSuccess.fromJson(
        response(asOfText: 'no price available'),
      );
      expect(r.priceAsOfText, 'no price available');
    });

    test('a missing date field says so, and never falls back to today', () {
      final json = response(price: 100.0, upside: 0.1);
      (json['company'] as Map<String, dynamic>).remove('price_as_of_text');
      final r = ValuationSuccess.fromJson(json);
      expect(r.priceAsOfText, 'close date unavailable');
      expect(r.priceAsOfText, isNot(contains('2026')));
    });
  });

  group('the headline card', () {
    testWidgets('shows the price and the date it closed on', (tester) async {
      await value(tester, response(price: 349.21, upside: -0.416));
      expect(find.text('\$349.21'), findsOneWidget);
      expect(find.text('close as of 20 November 2026'), findsOneWidget);
      expect(find.text('IMPLIED DOWNSIDE'), findsOneWidget);
      expect(find.text('-41.6%'), findsOneWidget);
    });

    testWidgets('says the price is unavailable and drops the comparison', (
      tester,
    ) async {
      await value(tester, response(asOfText: 'no price available'));

      expect(find.text('unavailable'), findsOneWidget);
      expect(find.text('no price available'), findsOneWidget);
      // No direction is claimed, in the label or in the figure.
      expect(find.text('IMPLIED UPSIDE'), findsNothing);
      expect(find.text('IMPLIED DOWNSIDE'), findsNothing);
      expect(find.text('VS MARKET PRICE'), findsOneWidget);
      expect(find.text('n/a'), findsOneWidget);
      // And nothing that reads as a measured zero.
      expect(find.text('+0.0%'), findsNothing);
      expect(find.text('0.0%'), findsNothing);
      expect(find.text('-0.0%'), findsNothing);
    });

    testWidgets('the valuation itself is still on screen', (tester) async {
      await value(tester, response(asOfText: 'no price available'));
      expect(find.textContaining('204.00'), findsWidgets);
    });

    testWidgets('the date is shown even when it is unknown', (tester) async {
      final json = response(price: 100.0, upside: 0.1);
      (json['company'] as Map<String, dynamic>).remove('price_as_of_text');
      await value(tester, json);
      expect(find.text('close date unavailable'), findsOneWidget);
    });
  });
}
