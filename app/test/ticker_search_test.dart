// Search-as-you-type must help without costing the backend a request per
// keystroke, and must never get in the way of typing a ticker and tapping
// Value. These tests run on the test framework's fake clock, so "300ms of
// silence" is exact rather than a sleep that hopes.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/ticker_search.dart';
import 'package:dcf_app/valuation_api.dart';

const _apple = CompanySearchResult(
  ticker: 'AAPL',
  name: 'Apple Inc.',
  exchange: 'NASDAQ',
);
const _bac = CompanySearchResult(
  ticker: 'BAC',
  name: 'Bank of America Corporation',
  exchange: 'NYSE',
);

/// A fetcher that records every query and answers from a script.
class FakeSearch {
  final List<String> queries = [];
  final Map<String, SearchOutcome> answers = {};
  final Map<String, Completer<SearchOutcome>> held = {};
  SearchOutcome fallback = const SearchResults([]);

  Future<SearchOutcome> call(String query) {
    queries.add(query);
    final hold = held[query];
    if (hold != null) return hold.future;
    return Future.value(answers[query] ?? fallback);
  }
}

/// Type [text] one character at a time, [gap] apart - the way a person does.
Future<void> typeSlowly(
  WidgetTester tester,
  TickerSearchController c,
  String text, {
  Duration gap = const Duration(milliseconds: 80),
}) async {
  for (var i = 1; i <= text.length; i++) {
    c.onQueryChanged(text.substring(0, i));
    await tester.pump(gap);
  }
}

void main() {
  group('debounce', () {
    testWidgets('typing a company name costs one request, not one per key', (
      tester,
    ) async {
      final fake = FakeSearch()
        ..answers['bank of am'] = const SearchResults([_bac]);
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      await typeSlowly(tester, c, 'bank of am');
      expect(fake.queries, isEmpty, reason: 'no pause yet, so nothing is sent');

      await tester.pump(const Duration(milliseconds: 300));

      expect(fake.queries, ['bank of am']);
      expect(c.phase, SearchPhase.results);
      expect(c.results.single.ticker, 'BAC');
    });

    testWidgets('nothing is sent until the pause is long enough', (
      tester,
    ) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 299));
      expect(fake.queries, isEmpty);

      await tester.pump(const Duration(milliseconds: 1));
      expect(fake.queries, ['apple']);
    });

    testWidgets('a pause mid-name searches, and typing on searches again', (
      tester,
    ) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      await typeSlowly(tester, c, 'bank');
      await tester.pump(const Duration(milliseconds: 400));
      await typeSlowly(tester, c, 'bank of am');
      await tester.pump(const Duration(milliseconds: 400));

      expect(fake.queries, ['bank', 'bank of am']);
    });

    testWidgets('clearing the field cancels a pending search', (tester) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 100));
      c.onQueryChanged('');
      await tester.pump(const Duration(seconds: 1));

      expect(fake.queries, isEmpty);
      expect(c.isOpen, isFalse);
    });

    testWidgets('an answer for text the field no longer holds is not shown', (
      tester,
    ) async {
      final fake = FakeSearch();
      final slow = Completer<SearchOutcome>();
      fake.held['bank'] = slow;
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('bank');
      await tester.pump(const Duration(milliseconds: 300)); // sent, held
      c.onQueryChanged('bank of am'); // typing moves on
      slow.complete(const SearchResults([_apple])); // late answer
      await tester.pump();

      expect(c.results.map((r) => r.ticker), isNot(contains('AAPL')));

      // And the current text is still searched once its own pause ends.
      await tester.pump(const Duration(milliseconds: 300));
      expect(fake.queries, ['bank', 'bank of am']);
    });
  });

  group('cache', () {
    testWidgets(
      'backspacing to an answered query costs nothing and is instant',
      (tester) async {
        final fake = FakeSearch()
          ..answers['bank'] = const SearchResults([_bac])
          ..answers['bank of am'] = const SearchResults([_bac]);
        final c = TickerSearchController(fetch: fake.call);
        addTearDown(c.dispose);

        c.onQueryChanged('bank');
        await tester.pump(const Duration(milliseconds: 300));
        c.onQueryChanged('bank of am');
        await tester.pump(const Duration(milliseconds: 300));
        expect(fake.queries, ['bank', 'bank of am']);

        c.onQueryChanged('bank'); // backspaced
        expect(c.phase, SearchPhase.results, reason: 'shown without waiting');
        expect(c.results.single.ticker, 'BAC');
        await tester.pump(const Duration(seconds: 1));

        expect(fake.queries, ['bank', 'bank of am'], reason: 'no new request');
      },
    );

    testWidgets('case and spacing do not make a query new', (tester) async {
      final fake = FakeSearch()
        ..answers['bank of am'] = const SearchResults([_bac]);
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('bank of am');
      await tester.pump(const Duration(milliseconds: 300));
      c.onQueryChanged('BANK  OF AM ');
      await tester.pump(const Duration(seconds: 1));

      expect(fake.queries, hasLength(1));
    });

    testWidgets('a late answer is still cached for later', (tester) async {
      final fake = FakeSearch();
      final slow = Completer<SearchOutcome>();
      fake.held['bank'] = slow;
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('bank');
      await tester.pump(const Duration(milliseconds: 300));
      c.onQueryChanged('bank of');
      slow.complete(const SearchResults([_bac]));
      await tester.pump();

      c.onQueryChanged('bank');
      expect(c.results.single.ticker, 'BAC');
      expect(fake.queries, ['bank']);
    });

    testWidgets('the cache is bounded', (tester) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call, cacheSize: 2);
      addTearDown(c.dispose);

      for (final q in ['a', 'b', 'c']) {
        c.onQueryChanged(q);
        await tester.pump(const Duration(milliseconds: 300));
      }
      c.onQueryChanged('a'); // evicted: asked again
      await tester.pump(const Duration(milliseconds: 300));

      expect(fake.queries, ['a', 'b', 'c', 'a']);
    });
  });

  group('outcomes', () {
    testWidgets('no matches is an answer, shown as such', (tester) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('zzzzqqq');
      await tester.pump(const Duration(milliseconds: 300));

      expect(c.phase, SearchPhase.results);
      expect(c.results, isEmpty);
      expect(c.isOpen, isTrue);
    });

    testWidgets('an unavailable search is not cached', (tester) async {
      final fake = FakeSearch()..answers['apple'] = const SearchUnavailable();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 300));
      expect(c.phase, SearchPhase.unavailable);

      fake.answers['apple'] = const SearchResults([_apple]);
      c.onQueryChanged('appl');
      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 300));

      expect(fake.queries, ['apple', 'apple']);
      expect(c.results.single.ticker, 'AAPL');
    });

    testWidgets('after a 429 it stops asking for a while, then resumes', (
      tester,
    ) async {
      var now = DateTime(2026, 9, 13, 12);
      final fake = FakeSearch()
        ..answers['apple'] = const SearchUnavailable(rateLimited: true);
      final c = TickerSearchController(
        fetch: fake.call,
        cooldown: const Duration(seconds: 10),
        clock: () => now,
      );
      addTearDown(c.dispose);

      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 300));
      expect(fake.queries, ['apple']);

      // Still cooling down: typing on does not hit the server again.
      c.onQueryChanged('microsoft');
      await tester.pump(const Duration(milliseconds: 300));
      expect(fake.queries, ['apple']);
      expect(c.phase, SearchPhase.unavailable);

      now = now.add(const Duration(seconds: 11));
      c.onQueryChanged('microsof');
      await tester.pump(const Duration(milliseconds: 300));
      expect(fake.queries, ['apple', 'microsof']);
    });
  });

  group('dismissing', () {
    testWidgets('a selected ticker written into the field does not reopen it', (
      tester,
    ) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.dismiss(currentText: 'BAC');
      c.onQueryChanged('BAC');
      await tester.pump(const Duration(seconds: 1));

      expect(fake.queries, isEmpty);
      expect(c.isOpen, isFalse);
    });

    testWidgets('typing something new after a dismissal searches again', (
      tester,
    ) async {
      final fake = FakeSearch();
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.dismiss(currentText: 'BAC');
      c.onQueryChanged('BA');
      await tester.pump(const Duration(milliseconds: 300));

      expect(fake.queries, ['ba']);
    });

    testWidgets('dismissing drops an answer already on its way', (
      tester,
    ) async {
      final fake = FakeSearch();
      final slow = Completer<SearchOutcome>();
      fake.held['apple'] = slow;
      final c = TickerSearchController(fetch: fake.call);
      addTearDown(c.dispose);

      c.onQueryChanged('apple');
      await tester.pump(const Duration(milliseconds: 300));
      c.dismiss(currentText: 'AAPL');
      slow.complete(const SearchResults([_apple]));
      await tester.pump();

      expect(c.isOpen, isFalse);
    });
  });

  test('a ticker is told apart from a company name', () {
    for (final t in ['AAPL', 'brk-b', 'BRK.B', 'A', '7203']) {
      expect(looksLikeTicker(t), isTrue, reason: t);
    }
    for (final t in ['BANK OF AMERICA', 'AT&T', '', 'ABCDEFGHIJKLM']) {
      expect(looksLikeTicker(t), isFalse, reason: t);
    }
  });

  group('ValuationApi.search', () {
    Future<SearchOutcome> searchWith(
      http.Response Function(http.Request) handler,
    ) {
      final api = ValuationApi(
        client: MockClient((r) async => handler(r)),
        baseUrl: 'http://test',
      );
      return api.search('bank of am');
    }

    test('sends the query and reads name, ticker and exchange', () async {
      late Uri seen;
      final outcome = await searchWith((r) {
        seen = r.url;
        return http.Response(
          jsonEncode({
            'query': 'bank of am',
            'results': [
              {
                'ticker': 'BAC',
                'name': 'Bank of America Corporation',
                'exchange': 'NYSE',
                'type': 'Equity',
              },
            ],
          }),
          200,
        );
      });

      expect(seen.path, '/search');
      expect(seen.queryParameters['q'], 'bank of am');
      final results = (outcome as SearchResults).results;
      expect(results.single.ticker, 'BAC');
      expect(results.single.name, 'Bank of America Corporation');
      expect(results.single.exchange, 'NYSE');
    });

    test('a 429 is reported as rate limiting, so the app backs off', () async {
      final outcome = await searchWith(
        (_) =>
            http.Response('{"code":"rate_limited","message":"slow down"}', 429),
      );

      expect(outcome, isA<SearchUnavailable>());
      expect((outcome as SearchUnavailable).rateLimited, isTrue);
    });

    test('a 502 is unavailable, not "no matches"', () async {
      final outcome = await searchWith(
        (_) => http.Response('{"code":"upstream_error","message":"down"}', 502),
      );

      expect(outcome, isA<SearchUnavailable>());
      expect((outcome as SearchUnavailable).rateLimited, isFalse);
    });

    test('a network failure never throws', () async {
      final api = ValuationApi(
        client: MockClient((_) => throw const SocketException('offline')),
        baseUrl: 'http://test',
      );

      expect(await api.search('apple'), isA<SearchUnavailable>());
    });
  });

  group('on the screen', () {
    late List<String> paths;
    late int searchStatus;

    ValuationApi fakeBackend() {
      final aapl =
          (jsonDecode(
            File('test/fixtures/method_cases.json').readAsStringSync(),
          ) as List<dynamic>).cast<Map<String, dynamic>>().firstWhere(
            (c) => c['ticker'] == 'AAPL',
          )['body'];

      return ValuationApi(
        baseUrl: 'http://test',
        client: MockClient((request) async {
          paths.add(request.url.path);
          if (request.url.path == '/search') {
            if (searchStatus != 200) {
              return http.Response(
                '{"code":"rate_limited","message":"x"}',
                searchStatus,
              );
            }
            final q = request.url.queryParameters['q']!.toLowerCase();
            final results = [
              if (q.startsWith('app'))
                {'ticker': 'AAPL', 'name': 'Apple Inc.', 'exchange': 'NASDAQ'},
              if (q.startsWith('app'))
                {
                  'ticker': 'APLE',
                  'name': 'Apple Hospitality REIT, Inc.',
                  'exchange': 'NYSE',
                },
              if (q.startsWith('bank'))
                {
                  'ticker': 'BAC',
                  'name': 'Bank of America Corporation',
                  'exchange': 'NYSE',
                },
            ];
            return http.Response(
              jsonEncode({'query': q, 'results': results}),
              200,
            );
          }
          if (request.url.path == '/valuation/AAPL') {
            return http.Response(jsonEncode(aapl), 200);
          }
          return http.Response('{"status":"ok"}', 200);
        }),
      );
    }

    setUp(() {
      paths = [];
      searchStatus = 200;
    });

    int searches() => paths.where((p) => p == '/search').length;

    testWidgets('typing a name shows matching companies with their tickers', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), 'apple');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();

      expect(find.byKey(const Key('search-dropdown')), findsOneWidget);
      expect(find.text('Apple Inc.'), findsOneWidget);
      expect(find.text('AAPL'), findsWidgets);
      expect(find.text('Apple Hospitality REIT, Inc.'), findsOneWidget);
      expect(searches(), 1);
    });

    testWidgets('tapping a result fills the ticker and values it', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), 'apple');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();

      await tester.tap(find.text('Apple Inc.'));
      await tester.pump();
      await tester.pump();

      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        'AAPL',
      );
      expect(paths, contains('/valuation/AAPL'));
      expect(find.byKey(const Key('search-dropdown')), findsNothing);
      await tester.pump(const Duration(seconds: 1));
      expect(
        searches(),
        1,
        reason: 'the chosen ticker is not searched for again',
      );
    });

    testWidgets('typing a ticker and tapping Value works exactly as before', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), 'AAPL');
      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();

      expect(paths, contains('/valuation/AAPL'));
      expect(find.byKey(const Key('search-dropdown')), findsNothing);
      await tester.pump(const Duration(seconds: 1));
      expect(searches(), 0, reason: 'Value was tapped before the pause ended');
    });

    testWidgets('when search is throttled, direct entry still works', (
      tester,
    ) async {
      searchStatus = 429;
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), 'AAPL');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();
      expect(find.textContaining('Search is unavailable'), findsOneWidget);

      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();

      expect(paths, contains('/valuation/AAPL'));
    });

    testWidgets('no matches says so and points to typing the ticker', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), 'zzzz');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();

      expect(find.textContaining('No matching companies'), findsOneWidget);
    });

    testWidgets(
      'a company name sent with Value points to the list, not a 404',
      (tester) async {
        await tester.pumpWidget(DcfApp(api: fakeBackend()));

        await tester.enterText(find.byType(TextField), 'bank of america');
        await tester.pump(const Duration(milliseconds: 300));
        await tester.pump();
        await tester.tap(find.widgetWithText(FilledButton, 'Value'));
        await tester.pump();

        expect(paths.where((p) => p.startsWith('/valuation')), isEmpty);
        expect(
          find.text('Pick a company from the list to value it.'),
          findsOneWidget,
        );
        expect(
          find.text('Bank of America Corporation'),
          findsOneWidget,
          reason: 'the list stays open to pick from',
        );
      },
    );

    testWidgets('names with spaces and punctuation can be typed', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: fakeBackend()));

      await tester.enterText(find.byType(TextField), "at&t moody's, inc");

      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        "AT&T MOODY'S, INC",
      );
    });
  });
}
