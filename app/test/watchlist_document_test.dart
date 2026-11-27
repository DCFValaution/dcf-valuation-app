/// Reading the stored watchlist document.
///
/// This existed as a private method on the Firestore store and was
/// therefore untested: every watchlist test ran against the in-memory
/// store and never came near it. Mutation testing found it — a mutant
/// that silently dropped every refused company from the list passed the
/// whole suite.
///
/// So the parsing is a free function now, and these tests are the only
/// thing standing between a change here and a watchlist that quietly
/// disagrees with the valuation screen.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/user_data_store.dart';

void main() {
  test('an empty or missing document is an empty watchlist', () {
    expect(watchlistFromDocument(null), isEmpty);
    expect(watchlistFromDocument(const {}), isEmpty);
  });

  test('the order is the reader’s, from `tickers`', () {
    // `rows` is a map and has no order of its own. Returning the
    // watchlist in map order would reshuffle it on every read.
    final rows = watchlistFromDocument(const {
      'tickers': ['KO', 'NVDA', 'AMZN'],
      'rows': {'NVDA': {}, 'AMZN': {}, 'KO': {}},
    });
    expect(rows.map((r) => r.ticker).toList(), ['KO', 'NVDA', 'AMZN']);
  });

  test('a ticker with no cached row still gets one', () {
    // A company only just added has nothing cached. It must show as "not
    // valued yet" rather than vanish until the first refresh lands.
    final rows = watchlistFromDocument(const {
      'tickers': ['KO'],
      'rows': {},
    });
    expect(rows.single.ticker, 'KO');
    expect(rows.single.lastValue, isNull);
  });

  test('a refused company keeps its row', () {
    // The mutant that escaped: dropping these made the watchlist disagree
    // with the valuation screen, which says out loud that no figure is
    // shown and why.
    final rows = watchlistFromDocument(const {
      'tickers': ['NVDA', 'TSLA'],
      'rows': {
        'NVDA': {'value': 219.33, 'price': 125.53},
        'TSLA': {'price': 431.20, 'refused': true},
      },
    });

    expect(rows.map((r) => r.ticker).toList(), ['NVDA', 'TSLA']);
    final tsla = rows.last;
    expect(tsla.refused, isTrue);
    expect(tsla.lastValue, isNull);
    expect(tsla.lastPrice, 431.20, reason: 'it still has a share price');
    expect(tsla.upside, isNull, reason: 'no value, so no upside');
  });

  test('the figures and the date come back as stored', () {
    final rows = watchlistFromDocument(const {
      'tickers': ['NVDA'],
      'rows': {
        'NVDA': {
          'name': 'NVIDIA Corporation',
          'value': 219.33,
          'price': 125.53,
          'as_of': '2026-11-20T00:00:00Z',
        },
      },
    });
    final row = rows.single;

    expect(row.name, 'NVIDIA Corporation');
    expect(row.lastValue, 219.33);
    expect(row.lastPrice, 125.53);
    expect(row.asOf?.toUtc().year, 2026);
    expect(row.upside, closeTo(219.33 / 125.53 - 1, 1e-9));
  });

  test('a row that is not a map does not lose the whole watchlist', () {
    // Firestore returns what was written, and a future version could
    // write something else here. Losing every row to one bad entry would
    // be the worst available outcome.
    final rows = watchlistFromDocument(const {
      'tickers': ['NVDA', 'KO'],
      'rows': {'NVDA': 'nonsense', 'KO': {'value': 73.40}},
    });

    expect(rows.length, 2);
    expect(rows.first.lastValue, isNull);
    expect(rows.last.lastValue, 73.40);
  });

  test('a non-string in `tickers` is skipped, not crashed on', () {
    final rows = watchlistFromDocument(const {
      'tickers': ['NVDA', 42, null, 'KO'],
      'rows': {},
    });
    expect(rows.map((r) => r.ticker).toList(), ['NVDA', 'KO']);
  });
}
