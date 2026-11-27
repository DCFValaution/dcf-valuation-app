/// Reading the allowance off a response, and signing requests.
///
/// The property these protect is the one the whole design rests on: THE
/// APP NEVER DECIDES. It reads a number the server sent and shows it. So
/// the interesting cases are the ones where the server said nothing, or
/// said something the app does not understand - because a client that
/// guessed in either of those would be a client that could be told not to
/// show the gate.
library;

import 'dart:convert';

import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/allowance.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

void main() {
  group('reading the allowance off a response', () {
    test('reads a metered caller', () {
      final got = Allowance.fromHeaders({
        'x-plan': 'anonymous',
        'x-valuations-window': 'lifetime',
        'x-valuations-limit': '3',
        'x-valuations-remaining': '2',
      });
      expect(got, isNotNull);
      expect(got!.plan, 'anonymous');
      expect(got.limit, 3);
      expect(got.remaining, 2);
      expect(got.metered, isTrue);
      expect(got.showCount, isTrue);
    });

    test('is null when the server said nothing about it', () {
      // Not a default. "The server did not mention it" and "you have none
      // left" must never collapse: one hides the pill, the other would
      // show "0 free valuations left" to somebody who is not metered.
      expect(Allowance.fromHeaders({}), isNull);
      expect(Allowance.fromHeaders({'content-type': 'application/json'}),
          isNull);
    });

    test('"none" is not a number, and is not zero', () {
      final got = Allowance.fromHeaders({
        'x-plan': 'free',
        'x-valuations-limit': 'none',
      })!;
      expect(got.limit, isNull);
      expect(got.metered, isFalse);
      expect(got.showCount, isFalse, reason: 'no pill for an unmetered plan');
    });

    test('hides the count when the server admits it is a guess', () {
      final got = Allowance.fromHeaders({
        'x-plan': 'anonymous',
        'x-valuations-limit': '3',
        'x-valuations-remaining': '3',
        'x-valuations-degraded': '1',
      })!;
      expect(got.degraded, isTrue);
      expect(got.showCount, isFalse,
          reason: 'better no number than a wrong one');
    });

    test('knows the last one, which is the only moment it is emphasised', () {
      Allowance at(int left) => Allowance.fromHeaders({
            'x-plan': 'anonymous',
            'x-valuations-limit': '3',
            'x-valuations-remaining': '$left',
          })!;
      expect(at(3).onLastOne, isFalse);
      expect(at(2).onLastOne, isFalse);
      expect(at(1).onLastOne, isTrue);
      expect(at(0).onLastOne, isFalse, reason: 'none left is the gate, not a '
          'warning');
    });

    test('survives a header name in any case', () {
      final got = Allowance.fromHeaders({
        'X-Plan': 'anonymous',
        'X-Valuations-Limit': '3',
        'X-Valuations-Remaining': '1',
      });
      expect(got?.remaining, 1);
    });

    test('ignores a value it cannot read rather than crashing', () {
      final got = Allowance.fromHeaders({
        'x-plan': 'anonymous',
        'x-valuations-limit': 'three',
        'x-valuations-remaining': 'lots',
      })!;
      expect(got.limit, isNull);
      expect(got.remaining, isNull);
      expect(got.showCount, isFalse);
    });
  });

  group('the client that signs every request', () {
    test('adds both tokens', () async {
      late http.BaseRequest seen;
      final client = AuthedClient(
        _Spy((request) => seen = request),
        tokens: () async => const AuthTokens(
            idToken: 'id-abc', appCheckToken: 'attest-xyz'),
      );

      await client.get(Uri.parse('https://example.test/valuation/AAPL'));

      expect(seen.headers['Authorization'], 'Bearer id-abc');
      expect(seen.headers['X-Firebase-AppCheck'], 'attest-xyz');
    });

    test('sends an unsigned request rather than failing when there are no '
        'tokens', () async {
      late http.BaseRequest seen;
      final client = AuthedClient(
        _Spy((request) => seen = request),
        tokens: () async => null,
      );

      final response =
          await client.get(Uri.parse('https://example.test/health'));

      expect(response.statusCode, 200);
      expect(seen.headers.containsKey('Authorization'), isFalse);
    });

    test('a token that throws does not fail the request', () async {
      // A token hiccup must not become a broken app. The request simply
      // goes out unattributed, where the per-address cap governs it.
      final client = AuthedClient(
        _Spy((_) {}),
        tokens: () async => throw StateError('no token today'),
      );
      final response =
          await client.get(Uri.parse('https://example.test/valuation/AAPL'));
      expect(response.statusCode, 200);
    });

    test('reports the allowance it saw', () async {
      Allowance? reported;
      final client = AuthedClient(
        _Spy((_) {}, headers: {
          'x-plan': 'anonymous',
          'x-valuations-limit': '3',
          'x-valuations-remaining': '1',
        }),
        onAllowance: (a) => reported = a,
      );

      await client.get(Uri.parse('https://example.test/valuation/AAPL'));

      expect(reported?.remaining, 1);
    });

    test('says nothing when the server said nothing', () async {
      var calls = 0;
      final client = AuthedClient(
        _Spy((_) {}),
        onAllowance: (_) => calls++,
      );
      await client.get(Uri.parse('https://example.test/health'));
      expect(calls, 0);
    });

    test('a listener that throws does not fail the request', () async {
      final client = AuthedClient(
        _Spy((_) {}, headers: {'x-plan': 'anonymous'}),
        onAllowance: (_) => throw StateError('bad listener'),
      );
      final response =
          await client.get(Uri.parse('https://example.test/valuation/AAPL'));
      expect(response.statusCode, 200);
    });
  });
}

/// An inner client that records the request and answers with fixed headers.
class _Spy extends http.BaseClient {
  _Spy(this.onSend, {this.headers = const {}});

  final void Function(http.BaseRequest) onSend;
  final Map<String, String> headers;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    onSend(request);
    return http.StreamedResponse(
      Stream.value(utf8.encode('{}')),
      200,
      headers: headers,
      request: request,
    );
  }
}
