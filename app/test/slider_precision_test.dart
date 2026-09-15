// Finer slider steps, and typing a value exactly.
//
// Both have to leave the rest of the panel as it was: provenance labels, the
// changed indicator, and above all the confirmation with the backend. So the
// screen tests below do not stop at the dialog - they follow a typed value to
// the request it produces.

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

AdjustableAssumption spec(List<AdjustableAssumption> specs, String name) =>
    specs.firstWhere((s) => s.name == name);

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

final List<AdjustableAssumption> allSpecs = [
  ...kAdjustable,
  ...kDdmAdjustable,
  ...kSpeculativeAdjustable,
];

void main() {
  group('step sizes', () {
    test('discount rates and terminal growth step by 0.1%', () {
      for (final s in [
        spec(kAdjustable, 'wacc'),
        spec(kAdjustable, 'terminal_growth'),
        spec(kDdmAdjustable, 'cost_of_equity'),
        spec(kDdmAdjustable, 'terminal_growth'),
        spec(kSpeculativeAdjustable, 'wacc'),
      ]) {
        expect(s.step, 0.001, reason: s.name);
      }
    });

    test('growth and margins step by 0.25%', () {
      for (final s in [
        spec(kAdjustable, 'revenue_growth'),
        spec(kAdjustable, 'operating_margin'),
        spec(kDdmAdjustable, 'dividend_growth'),
        spec(kSpeculativeAdjustable, 'target_operating_margin'),
        spec(kSpeculativeAdjustable, 'speculative_revenue_growth'),
      ]) {
        expect(s.step, 0.0025, reason: s.name);
      }
    });

    test('counts of years step by one, and are never fractional', () {
      for (final s in [
        spec(kDdmAdjustable, 'high_growth_years'),
        spec(kSpeculativeAdjustable, 'years_to_profitability'),
      ]) {
        expect(s.step, 1, reason: s.name);
        expect(s.isWholeNumber, isTrue);
        expect(s.divisions, 14, reason: 'one notch per year from 1 to 15');
        for (final raw in [1.0, 4.4, 4.6, 7.5, 14.9]) {
          final snapped = s.snap(raw);
          expect(snapped, snapped.roundToDouble(), reason: '$raw -> $snapped');
        }
      }
    });

    test('every notch of every slider is an exact step inside its range', () {
      for (final s in allSpecs) {
        expect(s.divisions, greaterThan(0), reason: s.name);
        expect(
          (s.min + s.divisions * s.step - s.max).abs(),
          lessThan(1e-9),
          reason: '${s.name}: the steps must land exactly on the maximum',
        );
        for (var i = 0; i <= s.divisions; i++) {
          final v = s.snap(s.min + i * (s.max - s.min) / s.divisions);
          expect(
            v,
            inInclusiveRange(s.min, s.max),
            reason: '${s.name} notch $i',
          );
        }
      }
    });

    test('a slider\'s float noise is snapped away before it travels', () {
      final wacc = spec(kAdjustable, 'wacc');

      expect(wacc.snap(0.10300000000000001), 0.103);
      expect(wacc.snap(0.1034), 0.103);
      expect(wacc.snap(0.1036), 0.104);
    });

    test('every step is finer than the "changed" threshold would hide', () {
      // The panel treats differences under 0.005% as unchanged. One notch
      // must always register as a change.
      for (final s in allSpecs) {
        expect(s.step, greaterThan(0.00005), reason: s.name);
      }
    });

    test('fine sliders are not drawn with hundreds of tick marks', () {
      expect(spec(kAdjustable, 'wacc').divisions, 160);
      expect(spec(kDdmAdjustable, 'high_growth_years').divisions, 14);
    });
  });

  group('typing a value', () {
    ExactValue parse(
      List<AdjustableAssumption> specs,
      String name,
      String text, [
      Map<String, double> current = const {
        'wacc': 0.107,
        'terminal_growth': 0.025,
        'cost_of_equity': 0.106,
      },
    ]) => parseExactValue(
      spec: spec(specs, name),
      text: text,
      current: current,
      specs: specs,
    );

    test('a rate is typed as a percentage, however it is written', () {
      for (final text in ['10.3', '10.3%', ' 10.30 % ', '10,3']) {
        expect(parse(kAdjustable, 'wacc', text).value, 0.103, reason: text);
      }
    });

    test('is held to a hundredth of a percent', () {
      expect(parse(kAdjustable, 'wacc', '10.314').value, 0.1031);
    });

    test('something that is not a number is rejected with an example', () {
      final r = parse(kAdjustable, 'wacc', 'ten');
      expect(r.value, isNull);
      expect(r.error, contains('Enter a number'));
    });

    test('out of range is rejected with the range, never clamped', () {
      final high = parse(kAdjustable, 'wacc', '25');
      expect(high.value, isNull);
      expect(high.error, 'Enter a value from 4.00% to 20.00%.');

      final low = parse(kAdjustable, 'wacc', '3.9');
      expect(low.value, isNull);
    });

    test('the ends of the range are allowed', () {
      expect(parse(kAdjustable, 'wacc', '4').value, 0.04);
      expect(parse(kAdjustable, 'wacc', '20').value, 0.20);
    });

    test('a count of years must be whole', () {
      final fractional = parse(
        kSpeculativeAdjustable,
        'years_to_profitability',
        '5.5',
      );
      expect(fractional.value, isNull);
      expect(fractional.error, contains('whole number'));

      expect(
        parse(kSpeculativeAdjustable, 'years_to_profitability', '6').value,
        6,
      );
      expect(parse(kDdmAdjustable, 'high_growth_years', '12 years').value, 12);
      expect(parse(kDdmAdjustable, 'high_growth_years', '16').value, isNull);
    });

    test(
      'terminal growth at or above WACC is refused in the panel\'s words',
      () {
        // Inside terminal growth's own 0-6% range, but above a 5% WACC.
        final r = parse(kAdjustable, 'terminal_growth', '5.5', {
          'wacc': 0.05,
          'terminal_growth': 0.025,
        });

        expect(r.value, isNull);
        expect(r.error, growthGuardMessage(kAdjustable));
        expect(r.error, contains('below WACC'));
      },
    );

    test('the range is checked before the guard', () {
      // 11% is outside terminal growth's range altogether, and that is the
      // more basic thing to tell someone.
      expect(
        parse(kAdjustable, 'terminal_growth', '11').error,
        'Enter a value from 0.00% to 6.00%.',
      );
    });

    test('so is a WACC typed at or below terminal growth', () {
      final r = parse(kAdjustable, 'wacc', '2.5', {
        'wacc': 0.107,
        'terminal_growth': 0.05,
      });
      // 2.5% is below the slider's own minimum, so pick one inside it.
      expect(r.value, isNull);
      final inRange = parse(kAdjustable, 'wacc', '4.5', {
        'wacc': 0.107,
        'terminal_growth': 0.05,
      });
      expect(inRange.error, growthGuardMessage(kAdjustable));
    });

    test('the DDM\'s guard names the cost of equity', () {
      final r = parse(kDdmAdjustable, 'terminal_growth', '5.5', {
        'cost_of_equity': 0.05,
        'terminal_growth': 0.025,
      });
      expect(r.error, contains('below the cost of equity'));
    });

    test('an unrelated field is not refused for a problem it did not cause', () {
      // Already dragged into g >= WACC; typing revenue growth must still work.
      final r = parse(kAdjustable, 'revenue_growth', '5', {
        'wacc': 0.05,
        'terminal_growth': 0.06,
      });
      expect(r.value, 0.05);
    });
  });

  group('on the screens', () {
    late List<http.Request> requests;

    ValuationApi backend() => ValuationApi(
      baseUrl: 'http://test',
      client: MockClient((request) async {
        requests.add(request);
        final path = request.url.path;
        http.Response ok(Map<String, dynamic> b) =>
            http.Response(jsonEncode(b), 200);
        if (path == '/valuation/AAPL') return ok(methodCase('AAPL'));
        if (path == '/valuation/JPM') return ok(methodCase('JPM'));
        if (path == '/valuation') {
          final ticker = (jsonDecode(request.body) as Map)['ticker'];
          return ok(methodCase(ticker as String));
        }
        if (path == '/valuation/RIVN/speculative') {
          return ok(speculativeCase('rivn_speculative'));
        }
        if (path == '/valuation/speculative') {
          return ok(speculativeCase('rivn_speculative_overridden'));
        }
        if (path == '/search') {
          return http.Response('{"query":"","results":[]}', 200);
        }
        return http.Response('{"status":"ok"}', 200);
      }),
    );

    setUp(() => requests = []);

    Map<String, dynamic>? lastOverrides(String path) {
      final posts = requests.where(
        (r) => r.method == 'POST' && r.url.path == path,
      );
      if (posts.isEmpty) return null;
      return (jsonDecode(posts.last.body) as Map<String, dynamic>)['overrides']
          as Map<String, dynamic>;
    }

    Future<void> value(WidgetTester tester, String ticker) async {
      await tester.enterText(find.byType(TextField), ticker);
      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();
    }

    Future<void> typeExact(
      WidgetTester tester,
      String name,
      String text,
    ) async {
      final target = find.byKey(ValueKey('exact-$name'));
      await tester.ensureVisible(target);
      await tester.tap(target);
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('exact-value-field')), text);
      await tester.tap(find.byKey(const Key('exact-value-set')));
      await tester.pumpAndSettle();
    }

    String shownValue(WidgetTester tester, String name) => tester
        .widget<Text>(
          find.descendant(
            of: find.byKey(ValueKey('exact-$name')),
            matching: find.byType(Text),
          ),
        )
        .data!;

    testWidgets(
      'DCF: WACC typed as exactly 10.3% is confirmed with the backend',
      (tester) async {
        await tester.pumpWidget(DcfApp(api: backend()));
        await value(tester, 'AAPL');

        await typeExact(tester, 'wacc', '10.3');
        expect(shownValue(tester, 'wacc'), '10.30%');
        expect(
          find.text('1 changed'),
          findsOneWidget,
          reason: 'changed indicator still works',
        );
        expect(
          find.textContaining(RegExp(r'^was \d')),
          findsOneWidget,
          reason: 'the derived value stays in view',
        );

        await tester.pump(const Duration(milliseconds: 400));
        await tester.pumpAndSettle();
        expect(lastOverrides('/valuation'), {'wacc': 0.103});
      },
    );

    testWidgets('DCF: dragging lands on a fine step, not a coarse one', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: backend()));
      await value(tester, 'AAPL');

      final waccSlider = find.descendant(
        of: find
            .ancestor(
              of: find.byKey(const ValueKey('exact-wacc')),
              matching: find.byType(Column),
            )
            .first,
        matching: find.byType(Slider),
      );
      await tester.ensureVisible(waccSlider);
      tester.widget<Slider>(waccSlider).onChanged!(0.10300000000000001);
      await tester.pump();

      expect(shownValue(tester, 'wacc'), '10.30%');
      final sliders = tester.widget<AssumptionSliders>(
        find.byType(AssumptionSliders),
      );
      expect(sliders.current['wacc'], 0.103, reason: 'snapped, no float noise');
    });

    testWidgets('DCF: a real finger drag inside the scrolling screen moves the '
        'slider by fine steps and confirms', (tester) async {
      // A genuine gesture, not a call to onChanged: the slider sits in a
      // vertical scroll view, and a horizontal drag has to win that contest.
      await tester.pumpWidget(DcfApp(api: backend()));
      await value(tester, 'AAPL');

      final row = find
          .ancestor(
            of: find.byKey(const ValueKey('exact-wacc')),
            matching: find.byType(Column),
          )
          .first;
      final slider = find.descendant(of: row, matching: find.byType(Slider));
      await tester.ensureVisible(slider);
      await tester.pumpAndSettle();
      final before = tester
          .widget<AssumptionSliders>(find.byType(AssumptionSliders))
          .current['wacc']!;

      await tester.timedDrag(
        slider,
        const Offset(12, 0),
        const Duration(milliseconds: 400),
      );
      await tester.pump();

      final after = tester
          .widget<AssumptionSliders>(find.byType(AssumptionSliders))
          .current['wacc']!;
      expect(after, isNot(before), reason: 'the drag moved it');
      final notches = (after - 0.04) / 0.001;
      expect(
        (notches - notches.round()).abs(),
        lessThan(1e-6),
        reason: 'lands on a 0.1% notch: $after',
      );

      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
      expect(
        lastOverrides('/valuation')?['wacc'],
        after,
        reason: 'a drag still confirms with the backend',
      );
    });

    testWidgets(
      'an out-of-range value is refused in the dialog and nothing is sent',
      (tester) async {
        await tester.pumpWidget(DcfApp(api: backend()));
        await value(tester, 'AAPL');
        final before = shownValue(tester, 'wacc');

        await typeExact(tester, 'wacc', '25');

        expect(
          find.text('Enter a value from 4.00% to 20.00%.'),
          findsOneWidget,
        );
        expect(
          find.byKey(const Key('exact-value-field')),
          findsOneWidget,
          reason: 'the dialog stays open to correct it',
        );
        await tester.tap(find.text('Cancel'));
        await tester.pumpAndSettle();
        await tester.pump(const Duration(milliseconds: 400));

        expect(shownValue(tester, 'wacc'), before);
        expect(lastOverrides('/valuation'), isNull);
      },
    );

    testWidgets(
      'a typed terminal growth above WACC shows the panel\'s message',
      (tester) async {
        await tester.pumpWidget(DcfApp(api: backend()));
        await value(tester, 'AAPL');

        // Lower WACC first, then type a terminal growth inside its own 0-6%
        // range that still exceeds it.
        await typeExact(tester, 'wacc', '5');
        await tester.pump(const Duration(milliseconds: 400));
        await tester.pumpAndSettle();
        await typeExact(tester, 'terminal_growth', '5.5');

        expect(find.text(growthGuardMessage(kAdjustable)), findsOneWidget);
        expect(lastOverrides('/valuation'), {
          'wacc': 0.05,
        }, reason: 'the refused growth was never sent');
      },
    );

    testWidgets('DDM: cost of equity typed as exactly 10.3% is confirmed', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: backend()));
      await value(tester, 'JPM');

      await typeExact(tester, 'cost_of_equity', '10.3');
      expect(shownValue(tester, 'cost_of_equity'), '10.30%');

      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
      expect(lastOverrides('/valuation'), {'cost_of_equity': 0.103});
    });

    testWidgets(
      'speculative: discount rate 10.3% and years 4 reach the backend',
      (tester) async {
        await tester.pumpWidget(
          MaterialApp(
            theme: buildLightTheme(),
            home: SpeculativeScreen(
              api: backend(),
              ticker: 'RIVN',
              companyName: 'Rivian',
            ),
          ),
        );
        await tester.pumpAndSettle();

        await typeExact(tester, 'wacc', '10.3');
        await tester.pump(const Duration(milliseconds: 400));
        await tester.pumpAndSettle();
        await typeExact(tester, 'years_to_profitability', '4');
        await tester.pump(const Duration(milliseconds: 400));
        await tester.pumpAndSettle();

        final overrides = lastOverrides('/valuation/speculative')!;
        expect(overrides['wacc'], 0.103);
        expect(overrides['years_to_profitability'], 4);
        expect(overrides['years_to_profitability'], isA<int>());
      },
    );
  });
}
