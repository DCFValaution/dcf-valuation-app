// The Advanced section, and the growth path it has to show.
//
// Two promises. The first is restraint: a user who never opens this section
// sees the screen exactly as it was before it existed, so the panel starts
// collapsed, the fade starts off, and nothing inside it reaches the backend
// until it is switched on.
//
// The second is visibility. A fade changes every projected year at once, so
// listing the rate applied in each one is the whole point - a model whose
// shape cannot be read back is not one anybody can argue with.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/advanced_panel.dart';
import 'package:dcf_app/dcf_engine.dart' show fadeExponential, fadeLinear;
import 'package:dcf_app/theme.dart';

Future<void> pumpPanel(
  WidgetTester tester, {
  bool expanded = false,
  bool fadeEnabled = false,
  int fadeStartYear = 3,
  String fadePattern = fadeLinear,
  int projectionYears = 10,
  List<double> growthPath = const [],
  void Function(bool)? onExpandedChanged,
  void Function(bool)? onFadeChanged,
  void Function(int)? onStartYearChanged,
  void Function(String)? onPatternChanged,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildLightTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AdvancedPanel(
            expanded: expanded,
            onExpandedChanged: onExpandedChanged ?? (_) {},
            fadeEnabled: fadeEnabled,
            onFadeChanged: onFadeChanged ?? (_) {},
            fadeStartYear: fadeStartYear,
            onStartYearChanged: onStartYearChanged ?? (_) {},
            fadePattern: fadePattern,
            onPatternChanged: onPatternChanged ?? (_) {},
            projectionYears: projectionYears,
            growthPath: growthPath,
          ),
        ),
      ),
    ),
  );
  await tester.pump();
}

void main() {
  group('out of the way until asked for', () {
    testWidgets('collapsed, with nothing inside it on screen', (tester) async {
      await pumpPanel(tester);

      expect(find.text('Advanced'), findsOneWidget);
      expect(find.byKey(const Key('fade-toggle')), findsNothing);
      expect(find.byKey(const Key('fade-start-year')), findsNothing);
      expect(find.byKey(const Key('fade-pattern')), findsNothing);
      expect(find.byKey(const Key('growth-path')), findsNothing);
    });

    testWidgets('opening it shows the toggle, still off', (tester) async {
      await pumpPanel(tester, expanded: true);

      expect(find.byKey(const Key('fade-toggle')), findsOneWidget);
      expect(tester.widget<Switch>(find.byKey(const Key('fade-toggle'))).value,
          isFalse);
      // The controls the fade governs stay hidden until it is on.
      expect(find.byKey(const Key('fade-start-year')), findsNothing);
      expect(find.byKey(const Key('fade-pattern')), findsNothing);
    });

    testWidgets('the header says this is an override, not the model',
        (tester) async {
      await pumpPanel(tester, expanded: true, fadeEnabled: true);
      expect(find.textContaining('your override, not the default model'),
          findsOneWidget);
    });

    testWidgets('tapping the header asks to open it', (tester) async {
      bool? asked;
      await pumpPanel(tester, onExpandedChanged: (v) => asked = v);
      await tester.tap(find.byKey(const Key('advanced-header')));
      expect(asked, isTrue);
    });
  });

  group('the controls', () {
    testWidgets('switching the fade on is reported', (tester) async {
      bool? asked;
      await pumpPanel(tester, expanded: true, onFadeChanged: (v) => asked = v);
      await tester.tap(find.byKey(const Key('fade-toggle')));
      expect(asked, isTrue);
    });

    testWidgets('with it on, the start year and pattern appear',
        (tester) async {
      await pumpPanel(tester, expanded: true, fadeEnabled: true);
      expect(find.byKey(const Key('fade-start-year')), findsOneWidget);
      expect(find.byKey(const Key('fade-pattern')), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
    });

    testWidgets('the start year cannot run past the horizon', (tester) async {
      // The glide needs a year to run in, so on a five-year projection the
      // last year that can hold the full rate is the fourth.
      await pumpPanel(tester,
          expanded: true, fadeEnabled: true, projectionYears: 5);
      final slider = tester.widget<Slider>(
        find.byKey(const Key('fade-start-year')),
      );
      expect(slider.max, 4);
      expect(slider.min, 1);
    });

    testWidgets('a start year beyond the horizon is pulled back into it',
        (tester) async {
      await pumpPanel(tester,
          expanded: true,
          fadeEnabled: true,
          projectionYears: 5,
          fadeStartYear: 9);
      expect(tester.widget<Slider>(find.byKey(const Key('fade-start-year'))).value,
          4);
    });

    testWidgets('choosing the other pattern is reported', (tester) async {
      String? asked;
      await pumpPanel(
        tester,
        expanded: true,
        fadeEnabled: true,
        onPatternChanged: (v) => asked = v,
      );
      await tester.tap(find.text('Exponential'));
      expect(asked, fadeExponential);
    });

    testWidgets('each pattern explains itself', (tester) async {
      await pumpPanel(tester, expanded: true, fadeEnabled: true);
      expect(find.textContaining('Equal steps down'), findsOneWidget);

      await pumpPanel(tester,
          expanded: true, fadeEnabled: true, fadePattern: fadeExponential);
      expect(find.textContaining('Equal-proportion decay'), findsOneWidget);
    });
  });

  group('the growth path, written out', () {
    testWidgets('every year is listed once the backend has answered',
        (tester) async {
      await pumpPanel(
        tester,
        expanded: true,
        fadeEnabled: true,
        growthPath: const [0.18, 0.18, 0.18, 0.1579, 0.1357, 0.025],
      );

      expect(find.byKey(const Key('growth-path')), findsOneWidget);
      expect(find.text('Yr1-3: 18.0%'), findsOneWidget);
      expect(find.text('Yr4: 15.8%'), findsOneWidget);
      expect(find.text('Yr6: 2.5%'), findsOneWidget);
      expect(find.textContaining('matches the terminal growth rate'),
          findsOneWidget);
    });

    testWidgets('and says so plainly before one has', (tester) async {
      await pumpPanel(tester, expanded: true, fadeEnabled: true);
      expect(find.byKey(const Key('growth-path-empty')), findsOneWidget);
    });

    test('consecutive years at one rate read as a range', () {
      expect(
        GrowthPathView.summarise(const [0.18, 0.18, 0.18, 0.15, 0.025]),
        ['Yr1-3: 18.0%', 'Yr4: 15.0%', 'Yr5: 2.5%'],
      );
    });

    test('a flat path is one range, which is what the fade being off looks like',
        () {
      expect(
        GrowthPathView.summarise(const [0.05, 0.05, 0.05, 0.05, 0.05]),
        ['Yr1-5: 5.0%'],
      );
    });

    test('a path that never repeats lists every year', () {
      expect(
        GrowthPathView.summarise(const [0.10, 0.08, 0.06, 0.04]),
        ['Yr1: 10.0%', 'Yr2: 8.0%', 'Yr3: 6.0%', 'Yr4: 4.0%'],
      );
    });

    test('an empty path produces nothing rather than a stray label', () {
      expect(GrowthPathView.summarise(const []), isEmpty);
    });
  });
}
