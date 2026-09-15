// What a person reads when something goes wrong.
//
// Error messages are the easiest place for engine output to reach the screen:
// a caught exception, an HTTP status, the word "backend". These tests pin that
// every failure path reads as a sentence for a person, that the 502 prefix is
// accurate for both of the cases that produce one, and that a listing which
// cannot be valued is not mistaken for a bad request.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

final _engineSpeak = RegExp(
  r'backend|HTTP|Exception|FormatException|SocketException|upstream|null|'
  r'\$e\b|_[a-z]+_',
);

ValuationApi apiAnswering(http.Response Function(http.Request) handler) =>
    ValuationApi(
      baseUrl: 'http://test',
      client: MockClient((r) async => handler(r)),
    );

void expectPlain(String message) {
  expect(
    _engineSpeak.hasMatch(message),
    isFalse,
    reason: 'engine-speak in: $message',
  );
}

void main() {
  group('the 502 prefix', () {
    test(
      'when the provider was reached but returned incomplete data',
      () async {
        final api = apiAnswering(
          (_) => http.Response(
            jsonEncode({
              'code': 'upstream_error',
              'message': "Yahoo Finance returned no financial statements for 'AAPL' just now.",
            }),
            502,
          ),
        );

        final result = await api.value('AAPL') as ValuationFailure;

        expect(result.kind, ValuationFailureKind.upstreamError);
        expect(result.message, startsWith(kDataUnavailablePrefix));
        // The old prefix claimed the provider could not be reached, which was
        // false here.
        expect(result.message, isNot(contains('could not reach')));
        expect(result.message, contains('returned no financial statements'));
      },
    );

    test('when the provider genuinely could not be reached', () async {
      final api = apiAnswering(
        (_) => http.Response(
          jsonEncode({
            'code': 'upstream_error',
            'message':
                "Could not reach Yahoo Finance to look up 'AAPL' just now.",
          }),
          502,
        ),
      );

      final result = await api.value('AAPL') as ValuationFailure;

      expect(result.message, startsWith(kDataUnavailablePrefix));
      expect(result.message, contains('Could not reach Yahoo Finance'));
    });

    test('claims neither cause itself', () {
      expect(kDataUnavailablePrefix, isNot(contains('reach')));
      expect(kDataUnavailablePrefix, isNot(contains('incomplete')));
      expectPlain(kDataUnavailablePrefix);
    });
  });

  group('a listing in another currency', () {
    const message =
        'TSM reports its results in TWD, but its shares trade in USD.\n'
        '  A value worked out from TWD figures cannot be compared with a share '
        'price in USD without converting between the two.';

    test('is its own kind of failure, not a bad request', () async {
      final api = apiAnswering(
        (_) => http.Response(
          jsonEncode({'code': 'unsupported_listing', 'message': message}),
          422,
        ),
      );

      final result = await api.value('TSM') as ValuationFailure;

      expect(result.kind, ValuationFailureKind.unsupportedListing);
      expect(result.message, contains('TWD'));
    });

    testWidgets('is headed accurately on screen', (tester) async {
      await tester.pumpWidget(
        DcfApp(
          api: apiAnswering(
            (r) => r.url.path == '/valuation/TSM'
                ? http.Response(
                    jsonEncode({
                      'code': 'unsupported_listing',
                      'message': message,
                    }),
                    422,
                  )
                : http.Response('{"status":"ok","query":"","results":[]}', 200),
          ),
        ),
      );
      await tester.enterText(find.byType(TextField), 'TSM');
      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();

      expect(find.text('This listing can’t be valued'), findsOneWidget);
      expect(find.text('Check the request'), findsNothing);
    });
  });

  group('every failure path reads as a sentence', () {
    final cases = <String, http.Response Function(http.Request)>{
      'an unreadable response': (_) => http.Response('<html>oops</html>', 500),
      'an unexpected status': (_) => http.Response('{"code":"teapot"}', 418),
      'a success in an unexpected shape': (_) =>
          http.Response('{"method":"dcf","company":{}}', 200),
      'a thrown exception': (_) => throw const FormatException('boom'),
    };

    for (final entry in cases.entries) {
      test('valuation: ${entry.key}', () async {
        final result = await apiAnswering(entry.value).value('AAPL');
        final message = (result as ValuationFailure).message;
        expectPlain(message);
        expect(message, isNot(contains('boom')));
        expect(message, isNot(contains('418')));
        expect(message, isNot(contains('500')));
      });

      test('speculative: ${entry.key}', () async {
        final result = await apiAnswering(entry.value).speculative('RIVN');
        if (result is SpeculativeFailure) {
          expectPlain(result.message);
          expect(result.message, isNot(contains('boom')));
        }
      });

      test('relative: ${entry.key}', () async {
        final result = await apiAnswering(entry.value).relative('JPM');
        if (result is RelativeFailure) {
          expectPlain(result.message);
          expect(result.message, isNot(contains('boom')));
        }
      });

      test('spreadsheet: ${entry.key}', () async {
        final result = await apiAnswering(entry.value).downloadExcel('AAPL');
        if (result is ExcelFailure) {
          expectPlain(result.message);
          expect(result.message, isNot(contains('boom')));
        }
      });
    }
  });
}
