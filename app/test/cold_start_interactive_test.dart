// The app is usable while a sleeping server is still waking.
//
// The free-tier backend takes up to a minute to answer its first request. The
// launch ping that starts it waking must never hold the screen hostage: the
// search bar, the example tickers and everything else have to work at once,
// and only a valuation - the thing that genuinely needs the server - may wait.
//
// Every test here runs with a server that never answers /health at all, which
// is the worst case of a cold start: if anything on screen were waiting on the
// ping, it would wait forever.

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

/// A backend that is asleep: /health never returns, and neither does a
/// valuation. Search answers at once, as it would from a server that is up.
class SleepingBackend {
  final List<String> paths = [];
  final Completer<http.Response> _health = Completer();

  bool get pinged => paths.contains('/health');

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) {
      paths.add(request.url.path);
      if (request.url.path == '/health') return _health.future;
      if (request.url.path == '/search') {
        return Future.value(
          http.Response(
            jsonEncode({
              'status': 'ok',
              'query': request.url.queryParameters['q'] ?? '',
              'results': [
                {
                  'ticker': 'AAPL',
                  'name': 'Apple Inc.',
                  'exchange': 'NASDAQ',
                  'type': 'Equity',
                },
              ],
            }),
            200,
            headers: const {'content-type': 'application/json'},
          ),
        );
      }
      // A valuation against a sleeping server: it has not answered yet.
      return Completer<http.Response>().future;
    }),
  );
}

/// Run the clock past the app's longest request timeout.
///
/// Against a server that never answers, the app's requests are each waiting on
/// a 90-second timeout. Unmounting and advancing the clock lets those fire, so
/// a test ends with nothing pending - which is itself the point: nothing the
/// app does on launch outlives the screen.
Future<void> drain(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox());
  await tester.pump(const Duration(minutes: 3));
}

void main() {
  testWidgets('the wake-up ping goes out without anyone tapping anything', (
    tester,
  ) async {
    final server = SleepingBackend();
    await tester.pumpWidget(DcfApp(api: server.api()));
    await tester.pump();

    expect(server.pinged, isTrue);

    await drain(tester);
  });

  testWidgets('the search bar takes text while the ping is unanswered', (
    tester,
  ) async {
    final server = SleepingBackend();
    await tester.pumpWidget(DcfApp(api: server.api()));
    await tester.pump();

    await tester.tap(find.byType(TextField));
    await tester.enterText(find.byType(TextField), 'AAPL');
    await tester.pump();

    expect(find.text('AAPL'), findsWidgets);
    final field = tester.widget<TextField>(find.byType(TextField));
    expect(field.enabled, isNot(false));

    await drain(tester);
  });

  testWidgets('search answers while the ping is still unanswered', (
    tester,
  ) async {
    final server = SleepingBackend();
    await tester.pumpWidget(DcfApp(api: server.api()));
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'app');
    // Past the search debounce.
    await tester.pump(const Duration(milliseconds: 600));
    await tester.pump();

    expect(server.paths, contains('/search'));
    expect(find.text('Apple Inc.'), findsOneWidget);

    await drain(tester);
  });

  testWidgets('the Value button is live immediately', (tester) async {
    final server = SleepingBackend();
    await tester.pumpWidget(DcfApp(api: server.api()));
    await tester.pump();

    final button = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Value'),
    );
    expect(button.onPressed, isNotNull);

    // Starting a valuation is the one thing allowed to wait on the server,
    // and it can be started straight away.
    await tester.enterText(find.byType(TextField), 'MSFT');
    await tester.tap(find.widgetWithText(FilledButton, 'Value'));
    await tester.pump();

    expect(server.paths.any((p) => p.startsWith('/valuation/')), isTrue);

    await drain(tester);
  });

  testWidgets('only a valuation waits, and says the server is waking', (
    tester,
  ) async {
    final server = SleepingBackend();
    await tester.pumpWidget(DcfApp(api: server.api()));
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'AAPL');
    await tester.tap(find.widgetWithText(FilledButton, 'Value'));
    await tester.pump();
    // Past the threshold at which a slow answer is explained as a cold start.
    await tester.pump(
      ValuationApi.wakingThreshold + const Duration(seconds: 1),
    );

    expect(find.text('Waking up the server'), findsOneWidget);

    await drain(tester);
  });
}
