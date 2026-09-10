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
