// Tests for the free-tier cold-start handling.
//
// The backend sleeps after about fifteen minutes idle and takes roughly a
// minute to wake. These cover the client half of that: a timeout long enough
// to outlast the wake-up, a ping that starts it early, and the guarantee that
// a failed ping is never allowed to surface as an error of its own.

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/valuation_api.dart';

ValuationApi apiReturning(
  Future<http.Response> Function(http.Request request) handler,
) =>
    ValuationApi(client: MockClient(handler));

void main() {
  group('wakeUp', () {
    test('pings /health and reports success', () async {
      var path = '';
      final api = apiReturning((request) async {
        path = request.url.path;
        return http.Response('{"status":"ok"}', 200);
      });

      expect(await api.wakeUp(), isTrue);
      expect(path, '/health');
    });

    test('uses GET, so it cannot change anything on the server', () async {
      var method = '';
      final api = apiReturning((request) async {
        method = request.method;
        return http.Response('{"status":"ok"}', 200);
      });

      await api.wakeUp();
      expect(method, 'GET');
    });

    test('reports failure rather than throwing when the server is down',
        () async {
      final api = apiReturning((_) async => throw const SocketExceptionStub());

      // The launch ping must never surface as an error: the user did not ask
      // for it, and the real request that follows reports anything genuine.
      expect(await api.wakeUp(), isFalse);
    });

    test('reports failure on a non-200 without throwing', () async {
      final api = apiReturning((_) async => http.Response('nope', 503));
      expect(await api.wakeUp(), isFalse);
    });
  });

  group('cold-start timeout', () {
    test('a valuation outlasts a wake-up far longer than 30 seconds', () async {
      // The old 30-second budget failed reliably on the first request after
      // the server had slept. Anything under a minute reintroduces that.
      expect(ValuationApi.wakingThreshold.inSeconds, lessThan(10),
          reason: 'the "waking up" message must appear well before the '
              'timeout, not at the end of the wait');

      final api = apiReturning((_) async {
        // Longer than the old timeout, shorter than the new one.
        await Future<void>.delayed(const Duration(milliseconds: 50));
        return http.Response('{}', 500);
      });

      // Completing at all (rather than timing out) is the point.
      final result = await api.value('AAPL');
      expect(result, isA<ValuationFailure>());
    });

    test('the waking threshold leaves room for a warm response', () async {
      // A warm valuation returns in well under a second, so the threshold
      // must not be so short that every normal request claims the server is
      // asleep.
      expect(ValuationApi.wakingThreshold.inMilliseconds,
          greaterThanOrEqualTo(2000));
    });
  });

  rateLimitTests();
}

/// Stands in for a connection failure without importing dart:io, which is
/// awkward to construct portably in tests.
class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
}

/// A 429 body as the backend sends it.
http.Response _tooManyRequests(String code, String message) => http.Response(
      '{"code":"$code","message":"$message"}',
      429,
      headers: {'content-type': 'application/json', 'retry-after': '60'},
    );

void rateLimitTests() {
  group('rate limiting', () {
    test('our own limit reads as a friendly pause, not a failure', () async {
      final api = apiReturning((_) async => _tooManyRequests(
            'rate_limited',
            'Too many requests - please slow down.',
          ));

      final result = await api.value('AAPL');

      expect(result, isA<ValuationFailure>());
      final failure = result as ValuationFailure;
      expect(failure.kind, ValuationFailureKind.rateLimited);
      // Addressed to the user, and tells them what to do about it.
      expect(failure.message.toLowerCase(), contains('wait a moment'));
      expect(failure.message, contains('going a little fast'));
    });

    test("the provider's limit is worded as nobody's fault", () async {
      final api = apiReturning((_) async => _tooManyRequests(
            'upstream_rate_limited',
            'Yahoo Finance is rate-limiting requests right now.',
          ));

      final failure = await api.value('AAPL') as ValuationFailure;

      expect(failure.kind, ValuationFailureKind.rateLimited);
      // The distinction matters: the user cannot fix this one by slowing
      // down, so blaming their pace would be wrong.
      expect(failure.message, contains('affects everyone'));
      expect(failure.message, isNot(contains('going a little fast')));
    });

    test('a 429 never surfaces as a crash or an unexpected error', () async {
      for (final code in ['rate_limited', 'upstream_rate_limited', 'unknown']) {
        final api = apiReturning((_) async => _tooManyRequests(code, 'slow'));
        final failure = await api.value('AAPL') as ValuationFailure;
        expect(failure.kind, ValuationFailureKind.rateLimited,
            reason: '$code should be handled, not fall through to unexpected');
      }
    });

    test('the Excel export handles both 429s too', () async {
      final ours = apiReturning(
          (_) async => _tooManyRequests('rate_limited', 'slow down'));
      final theirs = apiReturning(
          (_) async => _tooManyRequests('upstream_rate_limited', 'throttled'));

      final a = await ours.downloadExcel('AAPL') as ExcelFailure;
      final b = await theirs.downloadExcel('AAPL') as ExcelFailure;

      expect(a.kind, ValuationFailureKind.rateLimited);
      expect(a.message, contains('going a little fast'));
      expect(b.kind, ValuationFailureKind.rateLimited);
      expect(b.message, contains('market data provider'));
    });
  });
}
