// Return on invested capital, beside the cost of that capital.
//
// The figure is a diagnostic: it says whether the business earns more on the
// capital tied up in it than that capital costs, and it must not be mistaken
// for part of the valuation. Two promises are tested here. That it reads
// correctly when the backend sends one - the verdict leading, both
// percentages present - and, more importantly, that it is simply absent
// everywhere the backend did not send one: a bank on the dividend path, a
// company the model refused, and any backend old enough not to know about it.
//
// The payload blocks below are copied verbatim from the running backend for
// Apple and Southern Company, rather than invented, so the app is tested
// against what it is actually sent.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/ui.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

Map<String, dynamic> caseFor(String ticker) {
  final cases =
      jsonDecode(File('test/fixtures/method_cases.json').readAsStringSync())
          as List<dynamic>;
  return cases.cast<Map<String, dynamic>>().firstWhere(
        (c) => c['ticker'] == ticker,
      )['body'] as Map<String, dynamic>;
}

/// Apple, as the backend sends it: far above its cost of capital.
const aboveBlock = {
  'roic': 0.7050959664692905,
  'wacc': 0.11174495280878041,
  'spread': 0.5933510136605101,
  'verdict': 'above',
  'reads_as':
      'earns more on the capital in the business than that capital costs',
  'detail': 'median of 3 year(s): operating profit after tax at 15.6%, over '
      'total assets less non-debt current liabilities and cash',
};

/// Southern Company, a regulated utility: close enough to its cost of capital
/// that neither direction can be claimed.
const aboutBlock = {
  'roic': 0.04217131628358488,
  'wacc': 0.05543380190432793,
  'spread': -0.013262485620743052,
  'verdict': 'about',
  'reads_as': 'earns about what the capital in the business costs',
  'detail': 'median of 3 year(s): operating profit after tax at 16.6%, over '
      'total assets less non-debt current liabilities and cash',
};

const belowBlock = {
  'roic': 0.026,
  'wacc': 0.09,
  'spread': -0.064,
  'verdict': 'below',
  'reads_as':
      'earns less on the capital in the business than that capital costs',
  'detail': 'median of 3 year(s)',
};

ValuationSuccess withReturns(Map<String, dynamic>? block) =>
    ValuationSuccess.fromJson({...caseFor('AAPL'), 'capital_returns': block});

Future<void> pumpLine(WidgetTester tester, CapitalReturns returns) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildLightTheme(),
      home: Scaffold(body: CapitalReturnsLine(returns: returns)),
    ),
  );
}

void main() {
  group('reading the backend', () {
    test('a company above its cost of capital', () {
      final r = withReturns(aboveBlock).capitalReturns!;
      expect(r.verdict, CapitalVerdict.above);
      expect(r.roic, closeTo(0.7051, 1e-4));
      expect(r.wacc, closeTo(0.1117, 1e-4));
      expect(r.spread, greaterThan(0));
    });

    test('a utility about its cost of capital', () {
      final r = withReturns(aboutBlock).capitalReturns!;
      expect(r.verdict, CapitalVerdict.about);
      // Below on the arithmetic, but not by enough to claim a direction.
      expect(r.spread, lessThan(0));
      expect(r.spread.abs(), lessThan(0.02));
    });

    test('a business below its cost of capital', () {
      expect(withReturns(belowBlock).capitalReturns!.verdict,
          CapitalVerdict.below);
    });

    test('no block means no figure, not a zero', () {
      expect(withReturns(null).capitalReturns, isNull);
    });

    test('a backend that has never heard of it is not an error', () {
      // The recorded fixtures predate the feature, which is exactly the case
      // a deployed app meets while the backend is still rolling out.
      final parsed = ValuationSuccess.fromJson(caseFor('AAPL'));
      expect(parsed.capitalReturns, isNull);
      expect(parsed.intrinsicValuePerShare, greaterThan(0));
    });

    test('an unfamiliar verdict is refused rather than guessed at', () {
      final r = withReturns({...aboveBlock, 'verdict': 'spectacular'});
      expect(r.capitalReturns, isNull,
          reason: 'a wrong direction is worse than no line');
    });
  });

  group('what it renders', () {
    testWidgets('above: the verdict leads, both figures follow', (tester) async {
      await pumpLine(tester, withReturns(aboveBlock).capitalReturns!);

      expect(find.text('Earns above its cost of capital'), findsOneWidget);
      expect(
        find.text('Return on invested capital 70.5% vs cost of capital 11.2%'),
        findsOneWidget,
      );
      // It says out loud that it is not part of the valuation.
      expect(find.textContaining('not part of the valuation'), findsOneWidget);
    });

    testWidgets('about: neither direction is claimed', (tester) async {
      await pumpLine(tester, withReturns(aboutBlock).capitalReturns!);

      expect(find.text('Earns about its cost of capital'), findsOneWidget);
      expect(find.textContaining('Earns below'), findsNothing);
      expect(
        find.text('Return on invested capital 4.2% vs cost of capital 5.5%'),
        findsOneWidget,
      );
    });

    testWidgets('below: said plainly', (tester) async {
      await pumpLine(tester, withReturns(belowBlock).capitalReturns!);
      expect(find.text('Earns below its cost of capital'), findsOneWidget);
    });
  });
}
