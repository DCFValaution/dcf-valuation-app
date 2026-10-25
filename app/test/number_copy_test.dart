/// Numbers in the interface must not contradict the words beside them.
///
/// Fixed-point formatting keeps a sign that rounding has already made
/// meaningless. The worst of it was the implied upside badge, which rendered
/// "+-0.0%": negative zero compares equal to zero, so it took the positive
/// branch of a `>= 0` test and then printed its own minus sign.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/dcf_engine.dart';
import 'package:dcf_app/formatting.dart';

void main() {
  group('a figure that rounds to nothing is not negative', () {
    for (final value in [-0.0, -0.000001, -0.00004, -0.0004]) {
      test('$value reads as zero', () {
        expect(percent(value), '0.0%');
        expect(signedPercent(value), '0.0%');
      });
    }

    test('and does not claim a direction either', () {
      // "+0.0%" asserts a rise the figure does not support.
      expect(signedPercent(0.0), '0.0%');
    });
  });

  group('a figure that survives rounding keeps its sign', () {
    test('negative', () {
      expect(percent(-0.052), '-5.2%');
      expect(signedPercent(-0.052), '-5.2%');
    });

    test('positive', () {
      expect(signedPercent(0.153), '+15.3%');
      expect(percent(0.153), '15.3%');
    });

    test('at other precisions', () {
      expect(percent(0.05117, places: 2), '5.12%');
      expect(fixed(-1234.5, 2), '-1234.50');
      expect(fixed(-0.004, 2), '0.00');
    });
  });

  group('the engine mirrors the backend without its glitches', () {
    test('an amount under half a cent loses its minus', () {
      // It used to print "-0.00", a loss too small for the figure to show.
      expect(formatMoney(-0.001), '0.00');
      expect(formatMoney(-0.0), '0.00');
    });

    test('a real amount is unchanged, separators and all', () {
      expect(formatMoney(-1234567.891), '-1,234,567.89');
      expect(formatMoney(1234567.891), '1,234,567.89');
    });

    test('percentages too', () {
      expect(formatPercent2(-0.000001), '0.00%');
      expect(formatPercent2(0.05117), '5.12%');
    });
  });
}
