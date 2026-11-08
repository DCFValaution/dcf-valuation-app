/// Market multiples, beside a valuation they are not part of.
///
/// The card's whole job is to be reference information without being
/// mistaken for the model's answer, and to show a dash wherever a figure
/// does not exist rather than a zero or a negative multiple that reads
/// like a bargain.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/formatting.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/ui.dart';
import 'package:dcf_app/valuation_api.dart';

Widget wrap(Widget child) => MaterialApp(
  theme: buildLightTheme(),
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

const apple = MarketSnapshot(
  currency: 'USD',
  marketCap: 4975383019520,
  peRatio: 38.125,
  pbRatio: 44.386,
  dividendYield: 0.00316,
  revenueTtm: 478834000000,
  netMargin: 0.27604,
  eps: 8.98,
);

/// Crocs: profitable at the operating line, a loss after a write-down, and
/// paying no dividend.
const lossMaker = MarketSnapshot(
  currency: 'USD',
  marketCap: 6900000000,
  peRatio: null,
  pbRatio: 5.4,
  dividendYield: null,
  revenueTtm: 4000000000,
  netMargin: -0.0201,
  eps: -1.50,
);

void main() {
  group('parsing', () {
    test('a null field stays null rather than becoming zero', () {
      final s = MarketSnapshot.fromJson({
        'currency': 'USD',
        'market_cap': 1.0e9,
        'pe_ratio': null,
        'eps': null,
      })!;
      expect(s.peRatio, isNull);
      expect(s.eps, isNull);
      expect(s.marketCap, 1.0e9);
    });

    test('an entirely empty snapshot is no snapshot', () {
      // A card of seven dashes is worse than no card.
      expect(MarketSnapshot.fromJson({'currency': 'USD'}), isNull);
      expect(MarketSnapshot.fromJson(null), isNull);
    });
  });

  group('the whole path, response to widget', () {
    // The gap that let a dead panel ship. MarketSnapshot.fromJson was
    // tested, and MarketSnapshotCard was tested, and the line joining them
    // was missing: ValuationSuccess.fromJson never read the field, so the
    // card's null check was always false and nothing rendered. Both ends
    // passed their tests the whole time.
    Map<String, dynamic> response() => {
      'method': 'dcf',
      'company': {
        'ticker': 'AAPL',
        'company_name': 'Apple Inc.',
        'sector': 'Technology',
      },
      'intrinsic_value_per_share': 188.08,
      'current_price': 342.37,
      'upside_downside': -0.45,
      'note': 'n',
      'assumptions': <dynamic>[],
      'snapshot': {
        'currency': 'USD',
        'market_cap': 4975383019520.0,
        'pe_ratio': 38.125,
        'pb_ratio': 44.386,
        'dividend_yield': 0.00316,
        'revenue_ttm': 478834000000.0,
        'net_margin': 0.27604,
        'eps': 8.98,
      },
    };

    test('a valuation carries its snapshot off the wire', () {
      final result = ValuationSuccess.fromJson(response());
      expect(
        result.snapshot,
        isNotNull,
        reason:
            'the field has to survive the response parse, not just '
            'parse correctly when handed to it directly',
      );
      expect(result.snapshot!.peRatio, closeTo(38.125, 0.001));
      expect(result.snapshot!.eps, closeTo(8.98, 0.001));
    });

    test('and a response without one is simply without one', () {
      final json = response()..remove('snapshot');
      expect(ValuationSuccess.fromJson(json).snapshot, isNull);
    });

    testWidgets('the card renders from a parsed response', (tester) async {
      // End to end in one test: JSON in, pixels out.
      final result = ValuationSuccess.fromJson(response());
      await tester.pumpWidget(
        wrap(MarketSnapshotCard(snapshot: result.snapshot!)),
      );
      expect(find.text('Company at a glance'), findsOneWidget);
      expect(find.text(r'$5.0T'), findsOneWidget);
      expect(find.text('38.1x'), findsOneWidget);
    });
  });

  group('the card', () {
    testWidgets('shows every field for a company that has them', (
      tester,
    ) async {
      await tester.pumpWidget(wrap(const MarketSnapshotCard(snapshot: apple)));

      expect(find.text('Company at a glance'), findsOneWidget);
      expect(find.text(r'$5.0T'), findsOneWidget);
      expect(find.text('38.1x'), findsOneWidget);
      expect(find.text('44.4x'), findsOneWidget);
      expect(find.text(r'$478.8B'), findsOneWidget);
      expect(find.text('27.6%'), findsOneWidget);
      expect(find.text('0.3%'), findsOneWidget);
      expect(find.text(r'$8.98'), findsOneWidget);
    });

    testWidgets('says it is not part of the valuation', (tester) async {
      // The separation is the point: a market multiple is not a second
      // opinion from the model.
      await tester.pumpWidget(wrap(const MarketSnapshotCard(snapshot: apple)));
      expect(find.textContaining('not part of the valuation'), findsOneWidget);
    });

    testWidgets('a loss-maker gets dashes, not negative multiples', (
      tester,
    ) async {
      await tester.pumpWidget(
        wrap(const MarketSnapshotCard(snapshot: lossMaker)),
      );

      // Two dashes, and only two: the price/earnings ratio and the
      // dividend yield. Both are figures that do not exist.
      expect(find.text('—'), findsNWidgets(2));
      expect(
        find.text('0.0%'),
        findsNothing,
        reason:
            'a non-payer has no '
            'yield, which is not a yield of zero',
      );

      // The loss is a fact and is printed, in both the places it shows.
      // Hiding the per-share figure while printing the margin would be two
      // fields disagreeing about whether the company made money.
      expect(find.text('-2.0%'), findsOneWidget);
      expect(find.text(r'-$1.50'), findsOneWidget);
    });

    testWidgets('a foreign listing is labelled in its own currency', (
      tester,
    ) async {
      const baba = MarketSnapshot(
        currency: 'USD',
        marketCap: 272283680768,
        peRatio: 16.594,
        eps: 6.5957,
        revenueTtm: 153450761034,
        netMargin: 0.10346,
      );
      await tester.pumpWidget(wrap(const MarketSnapshotCard(snapshot: baba)));
      // Converted to the trading currency, so the EPS beside a dollar
      // price is in dollars - the bug this panel would otherwise have
      // shipped printed it as 44.00 yuan.
      expect(find.text(r'$6.60'), findsOneWidget);
      expect(find.text('16.6x'), findsOneWidget);
      expect(find.textContaining('in USD'), findsOneWidget);
    });
  });

  group('formatting', () {
    test('money shortens the way a reader says it', () {
      expect(compactMoney(4975383019520), r'$5.0T');
      expect(compactMoney(478834000000), r'$478.8B');
      expect(compactMoney(6900000000), r'$6.9B');
    });

    test('a loss is shortened the same way, with one sign', () {
      expect(compactMoney(-1400000000), r'-$1.4B');
    });

    test('a currency without a known symbol keeps its code', () {
      expect(currencySymbol('USD'), r'$');
      expect(currencySymbol('CHF'), 'CHF ');
    });
  });
}
