// Smoke tests for the ticker search screen.
//
// These render the widget tree only; no request is made, because the API is
// called on submit rather than on build.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/theme.dart';

void main() {
  testWidgets('shows the search field, Value button and first-run state',
      (WidgetTester tester) async {
    await tester.pumpWidget(const DcfApp());

    expect(find.byType(TextField), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Value'), findsOneWidget);
    expect(find.text('Value any listed company'), findsOneWidget);
  });

  group('disclaimer', () {
    testWidgets('is visible before any valuation, not hidden behind a menu',
        (WidgetTester tester) async {
      await tester.pumpWidget(const DcfApp());

      expect(find.byType(DisclaimerLine), findsOneWidget);
      expect(
        find.textContaining('not investment advice'),
        findsOneWidget,
        reason: 'the framing should be set before anyone sees a number',
      );
    });

    testWidgets('names the two things a user must be told', (tester) async {
      await tester.pumpWidget(const DcfApp());

      final text = tester
          .widget<Text>(find.descendant(
            of: find.byType(DisclaimerLine),
            matching: find.byType(Text),
          ))
          .data!;

      expect(text.toLowerCase(), contains('educational estimate'));
      expect(text.toLowerCase(), contains('not investment advice'));
      expect(text.toLowerCase(), contains('licensed professional'));
    });

    testWidgets('the full text is one tap away from the header',
        (WidgetTester tester) async {
      await tester.pumpWidget(const DcfApp());

      await tester.tap(find.byTooltip('About and disclaimer'));
      await tester.pumpAndSettle();

      expect(find.text('About these valuations'), findsOneWidget);
      expect(find.textContaining('not investment advice'), findsWidgets);
      expect(find.textContaining('licensed financial professional'),
          findsOneWidget);
    });

    testWidgets('tapping the short line opens the full text', (tester) async {
      await tester.pumpWidget(const DcfApp());

      // On the first-run screen the line sits below the feature list, so it
      // has to be scrolled into view before it can be tapped.
      await tester.ensureVisible(find.byType(DisclaimerLine));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DisclaimerLine));
      await tester.pumpAndSettle();

      expect(find.text('About these valuations'), findsOneWidget);
    });
  });

  testWidgets('typed text is upper-cased as the user types',
      (WidgetTester tester) async {
    await tester.pumpWidget(const DcfApp());

    await tester.enterText(find.byType(TextField), 'aapl');
    await tester.pump();

    // Asserted against the field's own contents rather than by searching the
    // whole tree: the first-run state lists AAPL as a worked example, so a
    // bare text search would match either one.
    final field = tester.widget<TextField>(find.byType(TextField));
    expect(field.controller?.text, 'AAPL');
  });

  testWidgets('no valuation figure is shown before a search',
      (WidgetTester tester) async {
    await tester.pumpWidget(const DcfApp());

    expect(find.text('INTRINSIC VALUE PER SHARE'), findsNothing);
  });

  group('theming', () {
    testWidgets('renders in light mode with semantic colours available',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildLightTheme(),
        home: Builder(builder: (context) {
          // Reading the extension proves it is registered; a missing one
          // would throw on the first `context.colors` call in a screen.
          final colors = context.colors;
          return Text('${colors.positive.toARGB32()}');
        }),
      ));
      expect(tester.takeException(), isNull);
    });

    testWidgets('renders in dark mode with semantic colours available',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: Builder(builder: (context) {
          final colors = context.colors;
          return Text('${colors.negative.toARGB32()}');
        }),
      ));
      expect(tester.takeException(), isNull);
    });

    testWidgets('the app follows the system theme setting',
        (WidgetTester tester) async {
      await tester.pumpWidget(const DcfApp());
      final app = tester.widget<MaterialApp>(find.byType(MaterialApp));

      expect(app.themeMode, ThemeMode.system);
      expect(app.theme, isNotNull);
      expect(app.darkTheme, isNotNull);
      expect(app.title, 'Intrinsic');
    });
  });
}
