/// Typing a revenue growth the slider cannot reach.
///
/// The slider is the plausible range and does not move. Typing is a
/// deliberate act, so it reaches much further - out to where the model
/// stops producing a meaningful number rather than to where the number
/// stops being believable. Everything between the two is computed and
/// annotated, never refused.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/theme.dart';

AdjustableAssumption get growth =>
    kAdjustable.firstWhere((s) => s.name == 'revenue_growth');

ExactValue parse(String text, {AdjustableAssumption? spec}) => parseExactValue(
  spec: spec ?? growth,
  text: text,
  current: const {'revenue_growth': 0.077, 'wacc': 0.1067},
  specs: kAdjustable,
);

Widget wrap(Widget child) => MaterialApp(
  theme: buildLightTheme(),
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

void main() {
  group('the slider itself is unchanged', () {
    test('its visible range is still the plausible one', () {
      // The whole point: someone who never types an extreme value sees
      // exactly the app they saw yesterday.
      expect(growth.min, 0.0);
      expect(growth.max, 0.25);
      expect(growth.step, 0.0025);
    });

    test('dragging cannot leave that range', () {
      expect(growth.snap(-5.0), 0.0);
      expect(growth.snap(9.0), 0.25);
    });
  });

  group('typed values outside the slider', () {
    test('a high one is accepted, not clamped', () {
      expect(parse('50').value, closeTo(0.50, 1e-9));
      expect(parse('250').value, closeTo(2.50, 1e-9));
    });

    test('a negative one is accepted', () {
      // A shrinking company is real, if unusual.
      expect(parse('-10').value, closeTo(-0.10, 1e-9));
      expect(parse('-0.5').value, closeTo(-0.005, 1e-9));
    });

    test('the outer bounds themselves are inside', () {
      expect(parse('-99').value, closeTo(-0.99, 1e-9));
      expect(parse('1000').value, closeTo(10.0, 1e-9));
    });
  });

  group('beyond the outer guard', () {
    test('below -99% is refused, because the forecast flips sign there', () {
      // Below -100% the revenue multiplier is negative and the projection
      // alternates between positive and negative revenue. That is not a
      // shrinking company; it is broken arithmetic.
      final r = parse('-150');
      expect(r.value, isNull);
      expect(r.error, contains('computable range'));
    });

    test('far above the ceiling is refused', () {
      final r = parse('100000');
      expect(r.value, isNull);
      expect(r.error, contains('computable range'));
    });

    test('the refusal quotes the typed range, not the slider range', () {
      // Quoting "0.00% to 25.00%" here would refuse a value the app would
      // in fact have computed, and send the user to the wrong number.
      final error = parse('-150').error!;
      expect(error, contains('-99.00%'));
      expect(error, contains('1000.00%'));
      expect(error, isNot(contains('25.00%')));
    });

    test('nonsense is still nonsense', () {
      expect(parse('abc').value, isNull);
      expect(parse('').value, isNull);
    });
  });

  group('other assumptions are untouched', () {
    test('operating margin still stops at its slider bounds', () {
      final margin = kAdjustable.firstWhere((s) => s.name == 'operating_margin');
      expect(margin.hasWiderTypedRange, isFalse);
      final r = parse('95', spec: margin);
      expect(r.value, isNull);
      expect(
        r.error,
        isNot(contains('computable range')),
        reason: 'the slider range IS the limit here, so it is what to quote',
      );
    });

    test('WACC still stops at its slider bounds', () {
      final wacc = kAdjustable.firstWhere((s) => s.name == 'wacc');
      expect(wacc.hasWiderTypedRange, isFalse);
      expect(parse('80', spec: wacc).value, isNull);
    });
  });

  group('the note', () {
    test('an ordinary value says nothing at all', () {
      for (final v in [0.0, 0.077, 0.25]) {
        expect(outOfNormalRangeNote(growth, v), isNull, reason: '$v');
      }
    });

    test('a high value says the valuation is likely unrealistic', () {
      final note = outOfNormalRangeNote(growth, 0.50)!;
      expect(note, contains('very unlikely'));
      expect(note, contains('unrealistic'));
    });

    test('a negative value says the company shrinks every year', () {
      final note = outOfNormalRangeNote(growth, -0.10)!;
      expect(note, contains('shrinks every year'));
      expect(note, contains('caution'));
    });

    test('it is a note, not a refusal', () {
      // Nothing in either wording tells the user the figure was not
      // produced, because it was.
      for (final v in [0.50, -0.10]) {
        final note = outOfNormalRangeNote(growth, v)!;
        expect(note.toLowerCase(), isNot(contains('cannot')));
        expect(note.toLowerCase(), isNot(contains('invalid')));
        expect(note.toLowerCase(), isNot(contains('error')));
      }
    });

    test('an assumption with no wider range never notes anything', () {
      final margin = kAdjustable.firstWhere((s) => s.name == 'operating_margin');
      expect(outOfNormalRangeNote(margin, 0.95), isNull);
    });
  });

  group('on screen', () {
    Widget panel(double value) => wrap(
      AssumptionSliders(
        specs: kAdjustable,
        current: {for (final s in kAdjustable) s.name: s.min}
          ..['revenue_growth'] = value,
        derived: const {'revenue_growth': 0.077},
        sources: const {'revenue_growth': 'derived'},
        busy: false,
        onChanged: (_, _) {},
        onChangeEnd: () {},
        onReset: () {},
        statusLine: const SizedBox.shrink(),
      ),
    );

    testWidgets('a high typed value shows the note', (tester) async {
      await tester.pumpWidget(panel(0.50));
      expect(
        find.byKey(const ValueKey('out-of-range-revenue_growth')),
        findsOneWidget,
      );
      expect(find.textContaining('very unlikely'), findsOneWidget);
    });

    testWidgets('a negative typed value shows the other note', (tester) async {
      await tester.pumpWidget(panel(-0.10));
      expect(find.textContaining('shrinks every year'), findsOneWidget);
    });

    testWidgets('an ordinary value shows nothing', (tester) async {
      await tester.pumpWidget(panel(0.077));
      expect(
        find.byKey(const ValueKey('out-of-range-revenue_growth')),
        findsNothing,
      );
    });

    testWidgets('the slider survives a value past its own ceiling', (
      tester,
    ) async {
      // The thumb pins to the end of the track while the figure above it
      // reads the real typed value. A Slider handed a value outside its
      // range asserts, so this is the test that it never is.
      await tester.pumpWidget(panel(2.50));
      expect(tester.takeException(), isNull);
      expect(find.text('250.00%'), findsOneWidget);
    });

    testWidgets('and one below its floor', (tester) async {
      await tester.pumpWidget(panel(-0.10));
      expect(tester.takeException(), isNull);
      expect(find.text('-10.00%'), findsOneWidget);
    });
  });
}
