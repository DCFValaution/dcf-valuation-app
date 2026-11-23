// The Advanced section, and the growth path it exists to show.
//
// It used to be a switch. The fade was an override - off by default, on if
// you asked - so the panel's first promise was restraint: a user who never
// opened it saw the screen exactly as it was before it existed.
//
// That is no longer the shape of the model. Growth runs at the latest year's
// own rate, then the measured trend for four years, then declines to the
// terminal rate - always, for every company. There is nothing to switch on,
// so the switch and the start-year control are gone and what remains is the
// path itself, the shape of its decline, and how growth is paid for.
//
// The promise that survives is the second one: a model whose shape cannot be
// read back is not one anybody can argue with.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/advanced_panel.dart';
import 'package:dcf_app/dcf_engine.dart' show fadeExponential, fadeLinear;
import 'package:dcf_app/theme.dart';

const _path = [0.32, 0.12, 0.12, 0.12, 0.12, 0.10, 0.09, 0.07, 0.06, 0.04];

Future<void> pumpPanel(
  WidgetTester tester, {
  bool expanded = false,
  String fadePattern = fadeLinear,
  int projectionYears = 10,
  List<double> growthPath = _path,
  bool declineFellBack = false,
  double? currentRoic,
  double wacc = 0.09,
  String reinvestmentMethod = kReinvestmentCapexPath,
  void Function(bool)? onExpandedChanged,
  void Function(String)? onPatternChanged,
  void Function(String)? onReinvestmentChanged,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildLightTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AdvancedPanel(
            expanded: expanded,
            onExpandedChanged: onExpandedChanged ?? (_) {},
            // Still accepted, still unused by the panel's own controls: the
            // fade's state belongs to the screen, and removing the
            // parameters would be a change to every caller for no gain.
            fadeEnabled: false,
            onFadeChanged: (_) {},
            fadeStartYear: 1,
            onStartYearChanged: (_) {},
            fadePattern: fadePattern,
            onPatternChanged: onPatternChanged ?? (_) {},
            projectionYears: projectionYears,
            growthPath: growthPath,
            declineFellBack: declineFellBack,
            currentRoic: currentRoic,
            wacc: wacc,
            reinvestmentMethod: reinvestmentMethod,
            onReinvestmentChanged: onReinvestmentChanged ?? (_) {},
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

      expect(find.byKey(const Key('advanced-panel')), findsOneWidget);
      expect(find.byKey(const Key('reinvestment-toggle')), findsNothing);
      expect(find.textContaining('Year 1 is the latest'), findsNothing);
    });

    testWidgets('the header says what is inside, not what is overridden', (
      tester,
    ) async {
      await pumpPanel(tester);

      // It used to announce "Growth fade on - your override, not the default
      // model", which was the point when the fade was an override. It is
      // the model now, so the header describes rather than warns.
      expect(find.textContaining('override'), findsNothing);
      expect(
        find.textContaining('The growth path, the shape of its decline'),
        findsOneWidget,
      );
    });

    testWidgets('opening it reports back rather than opening itself', (
      tester,
    ) async {
      bool? reported;
      await pumpPanel(tester, onExpandedChanged: (v) => reported = v);

      await tester.tap(find.byKey(const Key('advanced-header')));
      await tester.pump();

      expect(reported, isTrue);
      // And nothing appeared: the panel is told whether it is open, it does
      // not decide.
      expect(find.byKey(const Key('reinvestment-toggle')), findsNothing);
    });
  });

  group('the growth path', () {
    testWidgets('is shown without being asked for', (tester) async {
      await pumpPanel(tester, expanded: true);

      // No switch to find, because there is nothing to switch.
      expect(find.byKey(const Key('fade-toggle')), findsNothing);
      // The view collapses equal consecutive years into one range, so the
      // four held years read as one line rather than four identical ones.
      expect(find.textContaining('Yr1: 32.0%'), findsOneWidget);
      expect(find.textContaining('Yr2-5: 12.0%'), findsOneWidget);
      expect(find.textContaining('Yr10: 4.0%'), findsOneWidget);
    });

    testWidgets('says which years each rate covers', (tester) async {
      await pumpPanel(tester, expanded: true);

      expect(
        find.textContaining('Year 1 is the latest year’s own growth'),
        findsOneWidget,
      );
      expect(find.textContaining('reaching it exactly at year 10'), findsOneWidget);
    });

    testWidgets('the horizon in that sentence is the real one', (tester) async {
      await pumpPanel(tester, expanded: true, projectionYears: 7);
      expect(find.textContaining('at year 7'), findsOneWidget);
    });
  });

  group('the decline', () {
    testWidgets('offers both shapes and reports the choice', (tester) async {
      String? chosen;
      await pumpPanel(
        tester,
        expanded: true,
        onPatternChanged: (v) => chosen = v,
      );

      await tester.tap(find.text('Exponential'));
      await tester.pump();
      expect(chosen, fadeExponential);
    });

    testWidgets('says when an exponential decline could not be used', (
      tester,
    ) async {
      await pumpPanel(tester, expanded: true, declineFellBack: true);

      expect(
        find.textContaining('a ratio between two rates'),
        findsOneWidget,
      );
    });

    testWidgets('and says nothing when it could', (tester) async {
      await pumpPanel(tester, expanded: true);
      expect(find.textContaining('a ratio between two rates'), findsNothing);
    });
  });

  group('how growth is paid for', () {
    testWidgets('offers both methods, capital spending first', (tester) async {
      await pumpPanel(tester, expanded: true);

      expect(find.byKey(const Key('reinvestment-toggle')), findsOneWidget);
      expect(find.text('Capital spending'), findsOneWidget);
      expect(find.text('Sales to capital'), findsOneWidget);
    });

    testWidgets('reports a change rather than making it', (tester) async {
      String? chosen;
      await pumpPanel(
        tester,
        expanded: true,
        onReinvestmentChanged: (v) => chosen = v,
      );

      await tester.tap(find.text('Sales to capital'));
      await tester.pump();
      expect(chosen, kReinvestmentSalesToCapital);
    });
  });

  group('the reference figures', () {
    testWidgets('are shown beside the long-run return', (tester) async {
      await pumpPanel(tester, expanded: true, currentRoic: 0.324, wacc: 0.098);

      // The slider is unreadable without them: 20% means nothing until you
      // know the company earns 32.4% today.
      expect(find.textContaining('32.4%'), findsOneWidget);
      expect(find.textContaining('9.8%'), findsOneWidget);
    });

    testWidgets('are absent rather than invented where the books do not '
        'support them', (tester) async {
      await pumpPanel(tester, expanded: true, currentRoic: null);

      expect(find.textContaining('on its capital now'), findsNothing);
    });
  });
}
