/// The Advanced panel's horizon must be the backend's, not a leftover.
///
/// Found on a device, at phone width, against the real API: Nvidia's growth
/// path read "Yr1: 40.0% ... Yr10: 4.0%" and the sentence underneath it
/// promised the terminal rate "exactly at year 5". The path was right and
/// the sentence was wrong.
///
/// The cause was a lookup that outlived its source. The horizon used to be
/// a slider, so it was read out of the map built from the slider specs;
/// when the horizon became ten years for every company the slider went
/// away, the lookup found nothing, and it fell through to a hardcoded five.
///
/// The panel's own tests could not catch this: they pass the horizon in
/// directly, so they test the sentence, not the wiring behind it. This one
/// goes through the app with a backend that says ten.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';
import 'package:dcf_app/theme.dart';

final _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

/// The AAPL valuation, with the horizon and growth path the engine ships
/// now. The stored fixture predates the three-phase structure.
Map<String, dynamic> _bodyWithHorizon(int years) {
  final body = jsonDecode(
    jsonEncode(_cases['valuation_AAPL']['body']),
  ) as Map<String, dynamic>;

  final assumptions = (body['assumptions'] as List).cast<Map<String, dynamic>>();
  for (final a in assumptions) {
    if (a['name'] == 'projection_years') a['value'] = years.toDouble();
  }

  // A path of the stated length, declining to the terminal rate: year one,
  // four held years, then the glide.
  const first = 0.12, held = 0.08, terminal = 0.03;
  final path = <double>[first];
  for (var y = 2; y <= 5 && y <= years; y++) {
    path.add(held);
  }
  final steps = years - 5;
  for (var i = 1; i <= steps; i++) {
    path.add(held + (terminal - held) * (i / steps));
  }
  if (steps > 0) path[path.length - 1] = terminal;
  body['growth_path'] = path;
  return body;
}

ValuationApi _api(int years) => ValuationApi(
  baseUrl: 'http://test',
  client: MockClient((request) async {
    final path = request.url.path;
    if (path == '/valuation' ||
        (path.startsWith('/valuation/') && !path.endsWith('/relative'))) {
      return http.Response(
        jsonEncode(_bodyWithHorizon(years)),
        200,
        headers: const {'content-type': 'application/json'},
      );
    }
    return http.Response('{"status":"ok","query":"","results":[]}', 200);
  }),
);

Future<void> _valueAndOpenAdvanced(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField), 'AAPL');
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();

  final header = find.byKey(const Key('advanced-header'));
  await tester.ensureVisible(header);
  await tester.pump();
  await tester.tap(header);
  await tester.pump();
  await tester.pump(AppMotion.enter + const Duration(milliseconds: 20));
}

void main() {
  testWidgets('the horizon in the caption is the one the backend sent', (
    tester,
  ) async {
    await tester.pumpWidget(DcfApp(api: _api(10)));
    await _valueAndOpenAdvanced(tester);

    expect(
      find.textContaining('reaching it exactly at year 10'),
      findsOneWidget,
      reason: 'the backend forecasts ten years, so the sentence must say ten',
    );
    expect(
      find.textContaining('at year 5.'),
      findsNothing,
      reason: 'five was the horizon before it stopped being a control',
    );
  });

  testWidgets('and follows it when it is something else', (tester) async {
    /// Not decoration: a test that only ever sees ten would pass against a
    /// hardcoded ten as readily as against the real lookup.
    await tester.pumpWidget(DcfApp(api: _api(7)));
    await _valueAndOpenAdvanced(tester);

    expect(find.textContaining('reaching it exactly at year 7'), findsOneWidget);
  });
}
