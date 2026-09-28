// Getting back from a result to the empty home screen.
//
// Two ways in, one behaviour: the arrow in the toolbar, and the platform's
// own back - Android's button and gesture, and the browser's back button,
// which all arrive as a pop on the route. Either way the result is discarded
// and the screen is the one a fresh launch shows.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases =
    jsonDecode(File('test/fixtures/method_fit_cases.json').readAsStringSync())
        as Map<String, dynamic>;

Map<String, dynamic> _body(String name) =>
    _cases[name]['body'] as Map<String, dynamic>;

final _backArrow = find.byKey(const Key('home-back'));

/// The empty home screen names itself; a result never does.
final _homeMarker = find.text('Value any listed company');

Future<void> _valueAaplOnScreen(WidgetTester tester) async {
  final body = _body('aapl');
  final ticker = (body['company'] as Map<String, dynamic>)['ticker'] as String;
  await tester.pumpWidget(
    DcfApp(
      api: ValuationApi(
        baseUrl: 'http://test',
        client: MockClient(
          (r) async => r.url.path == '/valuation/$ticker'
              ? http.Response(jsonEncode(body), 200,
                  headers: const {'content-type': 'application/json'})
              : http.Response('{"status":"ok","query":"","results":[]}', 200),
        ),
      ),
    ),
  );
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
  await tester.pumpAndSettle();
}

/// What Android's back button and the browser's back button both deliver.
Future<void> _systemBack(WidgetTester tester) async {
  await tester
      .binding
      .defaultBinaryMessenger
      .handlePlatformMessage(
        'flutter/navigation',
        const JSONMethodCodec().encodeMethodCall(
          const MethodCall('popRoute'),
        ),
        (_) {},
      );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('the home screen carries no back arrow - there is no result yet',
      (tester) async {
    await tester.pumpWidget(
      DcfApp(
        api: ValuationApi(
          baseUrl: 'http://test',
          client: MockClient((r) async =>
              http.Response('{"status":"ok","query":"","results":[]}', 200)),
        ),
      ),
    );
    await tester.pump();

    expect(_backArrow, findsNothing);
    expect(_homeMarker, findsOneWidget);
  });

  testWidgets('a valued company shows the arrow, and it returns home',
      (tester) async {
    await _valueAaplOnScreen(tester);
    expect(find.text('Apple Inc.'), findsWidgets);
    expect(_backArrow, findsOneWidget);

    await tester.tap(_backArrow);
    await tester.pumpAndSettle();

    expect(_homeMarker, findsOneWidget);
    expect(find.text('Apple Inc.'), findsNothing);
    expect(_backArrow, findsNothing);
  });

  testWidgets('the ticker is cleared, so home is ready for a new search',
      (tester) async {
    await _valueAaplOnScreen(tester);
    await tester.tap(_backArrow);
    await tester.pumpAndSettle();

    final field = tester.widget<TextField>(find.byType(TextField));
    expect(field.controller?.text, isEmpty);
  });

  testWidgets('the platform back gesture returns home, and does not exit',
      (tester) async {
    await _valueAaplOnScreen(tester);
    await _systemBack(tester);

    expect(_homeMarker, findsOneWidget);
    expect(find.text('Apple Inc.'), findsNothing);
  });

  testWidgets('back from the relative tab lands on a clean intrinsic home',
      (tester) async {
    await _valueAaplOnScreen(tester);
    await tester.tap(find.text('Relative (market)'));
    await tester.pumpAndSettle();

    await tester.tap(_backArrow);
    await tester.pumpAndSettle();

    expect(_homeMarker, findsOneWidget);
    // Valuing again comes back on the intrinsic lens, not the relative one
    // the user happened to leave open.
    await tester.enterText(find.byType(TextField), 'AAPL');
    await tester.tap(find.widgetWithText(FilledButton, 'Value'));
    await tester.pump();
    await tester.pump();
    await tester.pumpAndSettle();
    expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
  });
}
