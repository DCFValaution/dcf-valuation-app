// A missing sector is left off the screen, never shown as a sector.
//
// The data source intermittently omits a company's sector, and the backend
// then reports the placeholder "Unknown". The valuation itself is fine, so the
// header should read just the ticker - not "AAPL · Unknown", and not a ticker
// with a separator dot and nothing after it.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

/// A real recorded valuation for [ticker], with its sector replaced.
Map<String, dynamic> recordedWithSector(String ticker, Object? sector) {
  final cases = jsonDecode(
    File('test/fixtures/method_cases.json').readAsStringSync(),
  ) as List<dynamic>;
  final body = jsonDecode(
    jsonEncode(
      cases.cast<Map<String, dynamic>>().firstWhere(
        (c) => c['ticker'] == ticker,
      )['body'],
    ),
  ) as Map<String, dynamic>;
  final company = body['company'] as Map<String, dynamic>;
  if (sector == null) {
    company.remove('sector');
  } else {
    company['sector'] = sector;
  }
  return body;
}

Future<void> valueOnScreen(
  WidgetTester tester,
  Map<String, dynamic> body,
) async {
  final ticker = (body['company'] as Map<String, dynamic>)['ticker'] as String;
  await tester.pumpWidget(
    DcfApp(
      api: ValuationApi(
        baseUrl: 'http://test',
        client: MockClient(
          (r) async => r.url.path == '/valuation/$ticker'
              ? http.Response(jsonEncode(body), 200)
              : http.Response('{"status":"ok","query":"","results":[]}', 200),
        ),
      ),
    ),
  );
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

void main() {
  group('displaySector', () {
    for (final missing in ['Unknown', 'unknown', '', '  ']) {
      test('is absent for ${jsonEncode(missing)}', () {
        final result = ValuationSuccess.fromJson(
          recordedWithSector('JPM', missing),
        );
        expect(result.displaySector, isNull);
      });
    }

    test('is absent when the field is missing altogether', () {
      final result = ValuationSuccess.fromJson(recordedWithSector('JPM', null));
      expect(result.displaySector, isNull);
    });

    test('is the sector when there is one', () {
      final result = ValuationSuccess.fromJson(
        recordedWithSector('JPM', 'Financial Services'),
      );
      expect(result.displaySector, 'Financial Services');
    });
  });

  group('on screen', () {
    testWidgets('a missing sector leaves just the ticker', (tester) async {
      await valueOnScreen(tester, recordedWithSector('JPM', 'Unknown'));

      expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
      expect(find.text('JPM'), findsWidgets);
      expect(find.text('Unknown'), findsNothing);
      expect(find.textContaining('Unknown'), findsNothing);
      expect(find.byKey(const Key('sector-separator')), findsNothing);
    });

    testWidgets('a known sector is still shown with its separator', (
      tester,
    ) async {
      await valueOnScreen(
        tester,
        recordedWithSector('JPM', 'Financial Services'),
      );

      expect(find.text('Financial Services'), findsOneWidget);
      expect(find.byKey(const Key('sector-separator')), findsOneWidget);
    });
  });
}
