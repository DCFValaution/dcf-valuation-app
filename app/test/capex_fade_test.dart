/// The capex fade.
///
/// A three-year median applied flat to every projected year understates a
/// company mid-buildout by construction - the median of a rising series is
/// its middle year, a level already left behind. Holding the current rate
/// forever is the opposite error. So the projection starts at what the
/// company spends now and glides to a normalized level.
///
/// This file guards the half of that which runs on the device. The preview
/// engine is shown while a slider is being dragged, and any difference
/// between it and the backend becomes a figure the backend then quietly
/// corrects - so the glide has to be the same glide, and the anchor has to
/// survive the trip from the response into the engine.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/dcf_engine.dart';

DcfAssumptions assume({
  double capexPct = 0.30,
  double? capexTerminalPct,
  int years = 5,
}) => DcfAssumptions(
  revenueGrowth: 0.08,
  operatingMargin: 0.30,
  taxRate: 0.18,
  daPct: 0.05,
  capexPct: capexPct,
  capexTerminalPct: capexTerminalPct,
  nwcPct: 0.02,
  wacc: 0.10,
  terminalGrowth: 0.025,
  projectionYears: years,
);

void main() {
  group('capexPath', () {
    test('with no anchor it is the starting rate, repeated', () {
      // Exactly what the engine did before the fade existed.
      expect(capexPath(assume()), everyElement(closeTo(0.30, 1e-12)));
      expect(capexPath(assume()).length, 5);
    });

    test('year one holds at the current rate', () {
      final path = capexPath(assume(capexPct: 0.34, capexTerminalPct: 0.16));
      expect(path.first, closeTo(0.34, 1e-12));
    });

    test('the final year is the anchor exactly', () {
      // Pinned, not merely arrived at: the perpetuity is built from this
      // year's cash flow, and a float's width of daylight is a seam.
      final path = capexPath(assume(capexPct: 0.3392, capexTerminalPct: 0.1607));
      expect(path.last, 0.1607);
    });

    test('the anchor is hit bit-for-bit where the arithmetic drifts', () {
      // The pin is not decoration. Linear interpolation at full progress is
      // the anchor in algebra but not always in floating point: this pair
      // lands on 0.08785100000000001. The perpetuity is built from the final
      // year's cash flow, so a last bit of daylight is a seam.
      final path = capexPath(
        assume(capexPct: 0.78147, capexTerminalPct: 0.087851, years: 3),
      );
      expect(path.last, 0.087851);
      const start = 0.78147, terminal = 0.087851, steps = 2;
      expect(start + (terminal - start) * (steps / steps), isNot(terminal),
          reason: 'the drift being guarded against is real');
    });

    test('the steps in between are equal', () {
      final path = capexPath(assume(capexPct: 0.40, capexTerminalPct: 0.10));
      final steps = <double>[
        for (var i = 1; i < path.length; i++) path[i - 1] - path[i],
      ];
      for (final step in steps) {
        expect(step, closeTo(steps.first, 1e-12));
      }
    });

    test('it reproduces the approved Alphabet glide', () {
      final path = capexPath(
        assume(capexPct: 0.3392, capexTerminalPct: 0.1607),
      );
      const want = [0.3392, 0.2946, 0.2499, 0.2053, 0.1607];
      for (var i = 0; i < want.length; i++) {
        expect(path[i], closeTo(want[i], 5e-5), reason: 'year ${i + 1}');
      }
    });

    test('it fades upward as readily as down', () {
      // Intel spends less now than its recent average. The glide is the same
      // machinery; only the sign of the move differs.
      final path = capexPath(assume(capexPct: 0.2288, capexTerminalPct: 0.4010));
      expect(path.first, closeTo(0.2288, 1e-12));
      expect(path.last, 0.4010);
      expect(path[1], greaterThan(path[0]));
    });

    test('a one-year projection has nowhere to glide', () {
      final path = capexPath(
        assume(capexPct: 0.30, capexTerminalPct: 0.10, years: 1),
      );
      expect(path, [0.30]);
    });

    test('it does not borrow the growth fade shape', () {
      // The two fades answer different questions. Moving the growth glide out
      // to year four must not quietly hold capital spending up for three more
      // years as well.
      final a = DcfAssumptions(
        revenueGrowth: 0.08,
        operatingMargin: 0.30,
        taxRate: 0.18,
        daPct: 0.05,
        capexPct: 0.40,
        capexTerminalPct: 0.10,
        nwcPct: 0.02,
        wacc: 0.10,
        terminalGrowth: 0.025,
        projectionYears: 5,
        fadeEnabled: true,
        fadeStartYear: 4,
      );
      expect(capexPath(a)[1], lessThan(0.40),
          reason: 'capex should already be gliding in year two');
    });
  });

  group('the anchor survives the round trip', () {
    test('an absent anchor stays absent, rather than becoming zero', () {
      // A zero anchor would fade every company to spending nothing. The
      // difference between "hold it flat" and "fade to nothing" must survive
      // a trip through the map the app holds assumptions in.
      final a = DcfAssumptions.fromMap(assume().toMap());
      expect(a.capexTerminalPct, isNull);
      expect(a.toMap().containsKey('capex_terminal_pct'), isFalse);
    });

    test('an anchor survives', () {
      final a = DcfAssumptions.fromMap(
        assume(capexPct: 0.34, capexTerminalPct: 0.16).toMap(),
      );
      expect(a.capexTerminalPct, closeTo(0.16, 1e-12));
    });

    test('copyWith keeps it while a slider moves', () {
      // This is the drag: capex_pct changes, everything else is carried.
      final a = assume(capexPct: 0.34, capexTerminalPct: 0.16)
          .copyWith({'capex_pct': 0.50});
      expect(a.capexPct, closeTo(0.50, 1e-12));
      expect(a.capexTerminalPct, closeTo(0.16, 1e-12));
    });
  });

  group('the projection records what it applied', () {
    test('each year carries its own intensity', () {
      final base = const BaseYearData(
        revenue: 100000,
        totalDebt: 5000,
        cash: 10000,
        shares: 1000,
        currentPrice: 100,
      );
      final result = runDcf(
        base,
        assume(capexPct: 0.30, capexTerminalPct: 0.10),
      );
      expect(result.years.first.capexPct, closeTo(0.30, 1e-12));
      expect(result.years.last.capexPct, 0.10);
      // And the cash line agrees with the rate beside it.
      for (final y in result.years) {
        expect(y.capex, closeTo(y.revenue * y.capexPct, 1e-6));
      }
    });

    test('a fading capex changes the answer', () {
      final base = const BaseYearData(
        revenue: 100000,
        totalDebt: 5000,
        cash: 10000,
        shares: 1000,
        currentPrice: 100,
      );
      final flat = runDcf(base, assume(capexPct: 0.30));
      final faded = runDcf(base, assume(capexPct: 0.30, capexTerminalPct: 0.10));
      expect(faded.intrinsicValuePerShare,
          greaterThan(flat.intrinsicValuePerShare),
          reason: 'spending less later is worth more');
    });
  });

  group('the slider', () {
    final capex = kAdjustable.firstWhere((s) => s.name == 'capex_pct');

    test('it exists at all', () {
      // It never did before: the API accepted the override and the app had
      // no control for it.
      expect(capex.label, 'Capital expenditure');
      expect(capex.help, contains('year 1'));
    });

    test('its visible range covers ordinary businesses', () {
      expect(capex.min, 0.0);
      expect(capex.max, 0.60);
    });

    test('typing reaches past it, for the companies that need it', () {
      // Oracle's trailing intensity is 105% of revenue.
      expect(capex.hasWiderTypedRange, isTrue);
      final r = parseExactValue(
        spec: capex,
        text: '105',
        current: const {'capex_pct': 0.30, 'wacc': 0.10},
        specs: kAdjustable,
      );
      expect(r.value, closeTo(1.05, 1e-9));
    });

    test('a value past the outer guard is still refused', () {
      final r = parseExactValue(
        spec: capex,
        text: '400',
        current: const {'capex_pct': 0.30, 'wacc': 0.10},
        specs: kAdjustable,
      );
      expect(r.value, isNull);
      expect(r.error, contains('computable range'));
    });

    test('a value past the slider warns but is accepted', () {
      expect(outOfNormalRangeNote(capex, 1.05), isNotNull);
      expect(outOfNormalRangeNote(capex, 0.30), isNull);
    });
  });
}
