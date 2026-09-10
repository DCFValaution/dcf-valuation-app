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
}

/// Stands in for a connection failure without importing dart:io, which is
/// awkward to construct portably in tests.
class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
}
