// Tests for the Excel export client.
//
// The backend's outcomes are mocked, so these run offline and cover the paths
// that are awkward to reach against a live server - a plan limit, a refusal,
// a truncated file, an error body arriving with a 200 status.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/valuation_api.dart';

const _xlsx =
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

/// A minimal well-formed .xlsx starts with the zip magic number "PK".
final List<int> _zipBytes = [0x50, 0x4B, 0x03, 0x04, ...List.filled(120, 0x41)];

ValuationApi apiReturning(
  http.Response Function(http.Request request) handler,
) => ValuationApi(client: MockClient((request) async => handler(request)));

void main() {
  group('successful export', () {
    test('returns bytes and the backend filename', () async {
      final api = apiReturning(
        (_) => http.Response.bytes(
          _zipBytes,
          200,
          headers: {
            'content-type': _xlsx,
            'content-disposition': 'attachment; filename="AAPL_DCF_Model.xlsx"',
          },
        ),
      );

      final result = await api.downloadExcel('AAPL');
      expect(result, isA<ExcelSuccess>());
      final success = result as ExcelSuccess;
      expect(success.filename, 'AAPL_DCF_Model.xlsx');
      expect(success.bytes.length, _zipBytes.length);
    });

    test(
      'falls back to a ticker-based filename when the header is absent',
      () async {
        final api = apiReturning((_) => http.Response.bytes(_zipBytes, 200));
        final result = await api.downloadExcel('msft');
        expect((result as ExcelSuccess).filename, 'MSFT_DCF_Model.xlsx');
      },
    );

    test('uses GET when there are no overrides', () async {
      late http.Request seen;
      final api = apiReturning((request) {
        seen = request;
        return http.Response.bytes(_zipBytes, 200);
      });

      await api.downloadExcel('AAPL');
      expect(seen.method, 'GET');
      expect(seen.url.path, '/valuation/AAPL/excel');
    });

    test('POSTs the overrides so the workbook matches the screen', () async {
      late http.Request seen;
      final api = apiReturning((request) {
        seen = request;
        return http.Response.bytes(_zipBytes, 200);
      });

      await api.downloadExcel('AAPL', overrides: {'wacc': 0.09});
      expect(seen.method, 'POST');
      expect(seen.url.path, '/valuation/excel');

      final body = jsonDecode(seen.body) as Map<String, dynamic>;
      expect(body['ticker'], 'AAPL');
      expect((body['overrides'] as Map)['wacc'], 0.09);
    });
  });

  group('refusals and errors', () {
    test(
      'a not-suitable company is its own outcome, not a generic error',
      () async {
        final api = apiReturning(
          (_) => http.Response(
            jsonEncode({
              'code': 'not_suitable',
              'message': 'A standard DCF is not suitable for Rivian.',
              'reasons': ['The company is loss-making.'],
            }),
            422,
            headers: {'content-type': 'application/json'},
          ),
        );

        final result = await api.downloadExcel('RIVN');
        expect(result, isA<ExcelNotSuitable>());
        expect((result as ExcelNotSuitable).message, contains('not suitable'));
      },
    );

    test('a listing that cannot be valued is not called a bad request', () async {
      final api = apiReturning(
        (_) => http.Response(
          jsonEncode({
            'code': 'unsupported_listing',
            'message':
                'TSM reports its results in TWD, but its shares trade in USD.',
          }),
          422,
        ),
      );

      final result = await api.downloadExcel('TSM');
      expect(
        (result as ExcelFailure).kind,
        ValuationFailureKind.unsupportedListing,
      );
      expect(result.message, contains('TWD'));
    });

    test(
      'a 402 is no longer a plan limit - that data source is gone',
      () async {
        // The old paid-plan gate cannot occur now. Should a 402 ever arrive, it
        // must read as unexpected, never as an invitation to upgrade.
        final api = apiReturning(
          (_) => http.Response(jsonEncode({'code': 'plan_limited'}), 402),
        );

        final result = await api.downloadExcel('BRK-B');
        expect((result as ExcelFailure).kind, ValuationFailureKind.unexpected);
        expect(result.message, isNot(contains('plan')));
      },
    );

    test('an unknown ticker says there is nothing to export', () async {
      final api = apiReturning(
        (_) => http.Response(jsonEncode({'code': 'ticker_not_found'}), 404),
      );

      final result = await api.downloadExcel('ZZZZ');
      expect(
        (result as ExcelFailure).kind,
        ValuationFailureKind.tickerNotFound,
      );
      expect(result.message, contains('nothing to export'));
    });

    test('a rate limit is distinguished from a hard failure', () async {
      final api = apiReturning((_) => http.Response('{}', 429));
      final result = await api.downloadExcel('AAPL');
      expect((result as ExcelFailure).kind, ValuationFailureKind.rateLimited);
    });

    test('an upstream failure surfaces the backend message', () async {
      final api = apiReturning(
        (_) => http.Response(
          jsonEncode({'code': 'upstream_error', 'message': 'provider down'}),
          502,
        ),
      );
      final result = await api.downloadExcel('AAPL');
      expect((result as ExcelFailure).kind, ValuationFailureKind.upstreamError);
      expect(result.message, contains('provider down'));
    });
  });

  group('content integrity', () {
    test(
      'a JSON body arriving with a 200 is rejected, not saved as .xlsx',
      () async {
        // Writing this to a .xlsx file would produce a download that fails to
        // open with no explanation, so it is caught before it reaches disk.
        final api = apiReturning(
          (_) => http.Response(
            jsonEncode({'detail': 'oops'}),
            200,
            headers: {'content-type': 'application/json'},
          ),
        );

        final result = await api.downloadExcel('AAPL');
        expect(result, isA<ExcelFailure>());
        expect(
          (result as ExcelFailure).message,
          contains('didn’t download correctly'),
        );
        expect(result.kind, ValuationFailureKind.unexpected);
      },
    );

    test('an empty 200 body is rejected', () async {
      final api = apiReturning((_) => http.Response.bytes([], 200));
      final result = await api.downloadExcel('AAPL');
      expect(result, isA<ExcelFailure>());
    });

    test('an empty ticker never reaches the network', () async {
      var called = false;
      final api = apiReturning((_) {
        called = true;
        return http.Response.bytes(_zipBytes, 200);
      });

      final result = await api.downloadExcel('   ');
      expect(called, isFalse);
      expect((result as ExcelFailure).kind, ValuationFailureKind.badRequest);
    });
  });
}
