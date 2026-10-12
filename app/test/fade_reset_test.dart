/// The fade panel must describe the figure above it.
///
/// A fresh valuation does not carry the fade's overrides, so if the stored
/// fade state survived one, the panel would claim "Growth fade on" over an
/// unfaded number - and the growth path's caption, which promises the final
/// year matches the terminal rate, would be false. Found on a device: the
/// toggle stayed blue, the export still counted the override, and the figure
/// was the plain derived one.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

final _cases =
    jsonDecode(File('test/fixtures/relative_cases.json').readAsStringSync())
        as Map<String, dynamic>;

class Backend {
  /// Every valuation request, as url + body, newest last.
  final List<String> requests = [];

  /// Whether the most recent valuation asked for a fade.
  bool get lastAskedForFade =>
      requests.isNotEmpty && requests.last.contains('fade_enabled');

  ValuationApi api() => ValuationApi(
        baseUrl: 'http://test',
        client: MockClient((request) async {
          final path = request.url.path;
          // Overrides travel as a POST to /valuation with the ticker in the
          // body; a plain valuation is a GET at /valuation/<ticker>.
          final isOverridden = path == '/valuation';
          if (isOverridden ||
              (path.startsWith('/valuation/') && !path.endsWith('/relative'))) {
            requests.add('${request.url}|${request.body}');
            final ticker = isOverridden
                ? (jsonDecode(request.body) as Map)['ticker'] as String
                : path.split('/')[2];
            final name = 'valuation_$ticker';
            if (_cases.containsKey(name)) {
              return http.Response(
                jsonEncode(_cases[name]['body']),
                _cases[name]['status_code'] as int,
                headers: const {'content-type': 'application/json'},
              );
            }
          }
          return http.Response('{"status":"ok","query":"","results":[]}', 200);
        }),
      );
}

Future<void> valueTicker(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

final _advancedHeader = find.byKey(const Key('advanced-header'));
final _fadeToggle = find.byKey(const Key('fade-toggle'));

bool fadeIsOn(WidgetTester tester) =>
    tester.widget<Switch>(_fadeToggle).value;

/// Opens Advanced and switches the fade on.
Future<void> turnFadeOn(WidgetTester tester) async {
  await tester.ensureVisible(_advancedHeader);
  await tester.pump();
  await tester.tap(_advancedHeader);
  await tester.pump();
  await tester.ensureVisible(_fadeToggle);
  await tester.pump();
  await tester.tap(_fadeToggle);
  await tester.pump();
  await tester.pump();
}

void main() {
  testWidgets('a fresh valuation puts the fade back to off', (tester) async {
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));

    await valueTicker(tester, 'AAPL');
    expect(find.byKey(const Key('advanced-panel')), findsOneWidget,
        reason: 'the DCF company should carry the Advanced section');

    await turnFadeOn(tester);
    expect(fadeIsOn(tester), isTrue, reason: 'the fade was switched on');
    expect(backend.lastAskedForFade, isTrue,
        reason: 'switching it on should ask the backend for a faded figure');

    // A different company, valued the way the button values it.
    await valueTicker(tester, 'CRM');

    expect(backend.lastAskedForFade, isFalse,
        reason: 'a fresh valuation does not carry the fade');

    // The section stays open across valuations, so the toggle is still on
    // screen - and must now read as off.
    await tester.ensureVisible(_fadeToggle);
    await tester.pump();
    expect(fadeIsOn(tester), isFalse,
        reason: 'so the panel must not still claim the fade is on');
  });

  testWidgets('re-valuing the same company puts the fade back to off',
      (tester) async {
    // The case found on the device: the ticker never changed, so nothing
    // looked like a new company, but the request was still a plain one.
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));

    await valueTicker(tester, 'AAPL');
    await turnFadeOn(tester);
    expect(fadeIsOn(tester), isTrue);

    await valueTicker(tester, 'AAPL');
    expect(backend.lastAskedForFade, isFalse);

    await tester.ensureVisible(_fadeToggle);
    await tester.pump();
    expect(fadeIsOn(tester), isFalse);
  });
}
