/// The Advanced panel must describe the figure above it.
///
/// Found on a device, when the fade was still an override: the toggle stayed
/// blue across a fresh valuation, the export still counted the override, and
/// the figure was the plain derived one. The panel claimed "Growth fade on"
/// over an unfaded number.
///
/// The fade is gone - the decline is structural now, so there is nothing to
/// leave switched on - but the obligation moved rather than disappeared.
/// How growth is paid for is an override in exactly the same way, and a
/// method left selected over a figure computed without it would be the same
/// lie in different words.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/advanced_panel.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

final _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

class Backend {
  /// Every valuation request, as url + body, newest last.
  final List<String> requests = [];

  /// Whether the most recent valuation asked for the sales-to-capital
  /// reinvestment method rather than the default capital-spending path.
  bool get lastAskedForSalesToCapital =>
      requests.isNotEmpty && requests.last.contains('sales_to_capital');

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
final _reinvestmentToggle = find.byKey(const Key('reinvestment-toggle'));

bool salesToCapitalIsSelected(WidgetTester tester) => tester
    .widget<SegmentedButton<String>>(_reinvestmentToggle)
    .selected
    .contains(kReinvestmentSalesToCapital);

/// Opens Advanced and switches the fade on.
Future<void> chooseSalesToCapital(WidgetTester tester) async {
  await tester.ensureVisible(_advancedHeader);
  await tester.pump();
  await tester.tap(_advancedHeader);
  await tester.pump();
  // The section expands over AppMotion.enter, and a control that is still
  // growing is not yet a target - on a device as much as here. Waiting the
  // expansion out is what a user does too.
  await tester.pump(AppMotion.enter + const Duration(milliseconds: 20));
  await tester.ensureVisible(_reinvestmentToggle);
  await tester.pump();
  await tester.tap(find.text('Sales to capital'));
  await tester.pump();
  await tester.pump();
}

void main() {
  testWidgets('a fresh valuation puts the reinvestment method back to the default', (tester) async {
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));

    await valueTicker(tester, 'AAPL');
    expect(
      find.byKey(const Key('advanced-panel')),
      findsOneWidget,
      reason: 'the DCF company should carry the Advanced section',
    );

    await chooseSalesToCapital(tester);
    expect(salesToCapitalIsSelected(tester), isTrue, reason: 'the method was chosen');
    expect(
      backend.lastAskedForSalesToCapital,
      isTrue,
      reason: 'choosing it should ask the backend for that method',
    );

    // A different company, valued the way the button values it.
    await valueTicker(tester, 'CRM');

    expect(
      backend.lastAskedForSalesToCapital,
      isFalse,
      reason: 'a fresh valuation does not carry the override',
    );

    // The section stays open across valuations, so the toggle is still on
    // screen - and must now read as off.
    await tester.ensureVisible(_reinvestmentToggle);
    await tester.pump();
    expect(
      salesToCapitalIsSelected(tester),
      isFalse,
      reason: 'so the panel must not still claim that method',
    );
  });

  testWidgets('re-valuing the same company puts it back too', (
    tester,
  ) async {
    // The case found on the device: the ticker never changed, so nothing
    // looked like a new company, but the request was still a plain one.
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));

    await valueTicker(tester, 'AAPL');
    await chooseSalesToCapital(tester);
    expect(salesToCapitalIsSelected(tester), isTrue);

    await valueTicker(tester, 'AAPL');
    expect(backend.lastAskedForSalesToCapital, isFalse);

    await tester.ensureVisible(_reinvestmentToggle);
    await tester.pump();
    expect(salesToCapitalIsSelected(tester), isFalse);
  });
}
