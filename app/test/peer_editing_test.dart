// Editing the relative view's peer group.
//
// The rules that matter: edits cost nothing until applied, an apply costs one
// request, an answer already seen costs none; provenance is always shown; the
// backend's minimums are respected rather than worked around; and none of it
// touches the intrinsic side. Every response is the real backend's, recorded
// by tools/generate_relative_fixtures.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/relative_controller.dart';
import 'package:dcf_app/relative_view.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> recorded(String name) =>
    _cases[name] as Map<String, dynamic>;

Map<String, dynamic> editedCase(
  String ticker,
  List<String> add,
  List<String> remove,
) {
  final key =
      '$ticker|add:${([...add]..sort()).join(',')}|remove:${([...remove]..sort()).join(',')}';
  final found = (_cases['edited'] as Map<String, dynamic>)[key];
  if (found == null) throw StateError('No recording for $key');
  return found as Map<String, dynamic>;
}

double? figureOf(RelativeController c) => c.report?.relativeValue;

const _companies = {
  'MSFT': 'Microsoft Corporation',
  'GOOGL': 'Alphabet Inc.',
  'META': 'Meta Platforms, Inc.',
  'AMZN': 'Amazon.com, Inc.',
  'MS': 'Morgan Stanley',
};

CompanySearchResult company(String t) => CompanySearchResult(
  ticker: t,
  name: _companies[t] ?? t,
  exchange: 'NASDAQ',
);

/// Answers valuations, relative views and searches from the recordings.
class Backend {
  final List<http.Request> requests = [];
  bool failRelativePosts = false;

  List<http.Request> get relativePosts => requests
      .where((r) => r.method == 'POST' && r.url.path == '/valuation/relative')
      .toList();
  int get relativeRequests =>
      requests.where((r) => r.url.path.contains('relative')).length;
  int count(String path) => requests.where((r) => r.url.path == path).length;

  static const _automatic = {'JPM': 'produced', 'AAPL': 'no_peers'};

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      final path = request.url.path;
      http.Response from(Map<String, dynamic> c) =>
          http.Response(jsonEncode(c['body']), c['status_code'] as int);

      if (path == '/valuation/relative') {
        if (failRelativePosts) {
          return http.Response(
            '{"code":"upstream_rate_limited","message":"Yahoo is throttling."}',
            429,
          );
        }
        final b = jsonDecode(request.body) as Map<String, dynamic>;
        return from(
          editedCase(
            b['ticker'] as String,
            (b['add_peers'] as List).cast<String>(),
            (b['remove_peers'] as List).cast<String>(),
          ),
        );
      }
      final rel = RegExp(r'^/valuation/([A-Z]+)/relative$').firstMatch(path);
      if (rel != null) return from(recorded(_automatic[rel.group(1)]!));
      final val = RegExp(r'^/valuation/([A-Z]+)$').firstMatch(path);
      if (val != null) return from(recorded('valuation_${val.group(1)}'));
      if (path == '/search') {
        final q = request.url.queryParameters['q']!.toUpperCase();
        final results = [
          for (final e in _companies.entries)
            if (e.key.startsWith(q) || e.value.toUpperCase().startsWith(q))
              {
                'ticker': e.key,
                'name': e.value,
                'exchange': 'NASDAQ',
                'type': 'Equity',
              },
        ];
        return http.Response(jsonEncode({'query': q, 'results': results}), 200);
      }
      return http.Response('{"status":"ok"}', 200);
    }),
  );
}

Future<RelativeController> opened(Backend backend, String ticker) async {
  final c = RelativeController(api: backend.api(), ticker: ticker);
  c.open();
  await pumpEventQueue();
  return c;
}

void main() {
  group('staging and applying', () {
    test(
      'edits send nothing until applied, then exactly one request',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'AAPL');
        final before = backend.relativeRequests;

        expect(c.addPeer(company('MSFT')), isNull);
        expect(c.addPeer(company('GOOGL')), isNull);
        expect(c.addPeer(company('META')), isNull);
        expect(backend.relativeRequests, before, reason: 'staging is free');
        expect(c.hasPendingChanges, isTrue);

        await c.apply();

        expect(backend.relativeRequests, before + 1);
        final sent = jsonDecode(
          backend.relativePosts.single.body,
        ) as Map<String, dynamic>;
        expect(sent, {
          'ticker': 'AAPL',
          'add_peers': ['MSFT', 'GOOGL', 'META'],
          'remove_peers': <String>[],
        });
        expect(c.hasPendingChanges, isFalse);
      },
    );

    test(
      'AAPL: no automatic peers, and a figure once three real ones are added',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'AAPL');
        expect(c.report!.hasFigure, isFalse);
        expect(c.rows, isEmpty);

        for (final t in ['MSFT', 'GOOGL', 'META']) {
          c.addPeer(company(t));
        }
        await c.apply();

        expect(c.report!.hasFigure, isTrue);
        expect(c.rows.map((r) => r.ticker), ['MSFT', 'GOOGL', 'META']);
        expect(c.rows.every((r) => r.provenance == 'Added by you'), isTrue);
      },
    );

    test('too few added peers still declines honestly', () async {
      final backend = Backend();
      final c = await opened(backend, 'AAPL');

      c.addPeer(company('MSFT'));
      c.addPeer(company('GOOGL'));
      await c.apply();

      expect(
        c.report!.hasFigure,
        isFalse,
        reason: 'two peers are not a median',
      );
      expect(
        c.report!.declineReasons.single,
        // The user shaped this group, so the decline says these companies
        // could not all be compared rather than that none could be found.
        contains('Only 2 of these companies could be compared'),
      );
      expect(c.rows.map((r) => r.ticker), [
        'MSFT',
        'GOOGL',
      ], reason: 'the peers still show, so the user can add another');
    });

    test(
      'removing a peer changes the figure, and undoing it costs no request',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'AAPL');
        for (final t in ['MSFT', 'GOOGL', 'META']) {
          c.addPeer(company(t));
        }
        await c.apply();
        final three = figureOf(c)!;

        c.addPeer(company('AMZN'));
        await c.apply();
        final four = figureOf(c)!;
        expect(four, isNot(three), reason: 'the peer group moved the median');

        c.removePeer('AMZN');
        expect(
          c.rows.firstWhere((r) => r.ticker == 'AMZN').state,
          PeerRowState.toRemove,
        );
        final requests = backend.relativeRequests;
        await c.apply();

        expect(figureOf(c), three);
        expect(
          backend.relativeRequests,
          requests,
          reason: 'that group was already answered',
        );
      },
    );

    test(
      'the same group reached in a different order hits the cache',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'AAPL');
        for (final t in ['MSFT', 'GOOGL', 'META']) {
          c.addPeer(company(t));
        }
        await c.apply();
        await c.resetToAutomatic();

        final requests = backend.relativeRequests;
        for (final t in ['META', 'MSFT', 'GOOGL']) {
          c.addPeer(company(t));
        }
        await c.apply();

        expect(backend.relativeRequests, requests);
        expect(c.report!.hasFigure, isTrue);
      },
    );

    test('discarding staged edits returns to the applied group', () async {
      final backend = Backend();
      final c = await opened(backend, 'JPM');

      c.removePeer('WFC');
      c.addPeer(company('MS'));
      c.discard();

      expect(c.hasPendingChanges, isFalse);
      expect(c.rows.every((r) => r.state == PeerRowState.current), isTrue);
    });

    test(
      'applying twice while the first is in flight sends one request',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'JPM');
        c.addPeer(company('MS'));
        final before = backend.relativeRequests;

        final first = c.apply();
        final second = c.apply();
        await Future.wait([first, second]);

        expect(backend.relativeRequests, before + 1);
      },
    );
  });

  group('provenance and reset', () {
    test('JPM: automatic peers and an added one are labelled apart', () async {
      final backend = Backend();
      final c = await opened(backend, 'JPM');
      c.addPeer(company('MS'));
      await c.apply();

      final byTicker = {for (final r in c.rows) r.ticker: r};
      expect(byTicker['C']!.provenance, 'Chosen automatically');
      expect(byTicker['BAC']!.provenance, 'Chosen automatically');
      expect(byTicker['WFC']!.provenance, 'Chosen automatically');
      expect(byTicker['MS']!.provenance, 'Added by you');
      expect(c.report!.peerSelection!.mode, 'automatic, edited by you');
    });

    test(
      'JPM: removing an automatic peer leaves too few, and it can be restored',
      () async {
        final backend = Backend();
        final c = await opened(backend, 'JPM');
        final automatic = figureOf(c)!;

        c.removePeer('WFC');
        await c.apply();

        expect(c.report!.hasFigure, isFalse);
        final wfc = c.rows.firstWhere((r) => r.ticker == 'WFC');
        expect(
          wfc.state,
          PeerRowState.removed,
          reason: 'still visible, so it can be put back',
        );
        expect(
          wfc.name,
          'Wells Fargo & Company',
          reason: 'keeps its name once gone',
        );

        c.restorePeer('WFC');
        final requests = backend.relativeRequests;
        await c.apply();

        expect(figureOf(c), automatic);
        expect(
          backend.relativeRequests,
          requests,
          reason: 'the automatic answer was cached',
        );
      },
    );

    test('reset returns to the automatic group, from cache', () async {
      final backend = Backend();
      final c = await opened(backend, 'JPM');
      final automatic = figureOf(c)!;
      c.addPeer(company('MS'));
      await c.apply();
      expect(c.isEdited, isTrue);

      final requests = backend.relativeRequests;
      await c.resetOrDiscard();

      expect(c.isEdited, isFalse);
      expect(figureOf(c), automatic);
      expect(
        c.rows.every((r) => r.provenance == 'Chosen automatically'),
        isTrue,
      );
      expect(backend.relativeRequests, requests);
    });

    test('a company the user added is not also listed as "left out"', () async {
      // The backend reports MSFT as excluded from AAPL's automatic selection
      // even once the user has added it.
      final backend = Backend();
      final c = await opened(backend, 'AAPL');
      for (final t in ['MSFT', 'GOOGL', 'META']) {
        c.addPeer(company(t));
      }
      await c.apply();

      final raw = c.report!.peerSelection!.excluded.map((e) => e.ticker);
      expect(
        raw,
        contains('MSFT'),
        reason: 'fixture sanity: the backend does list it',
      );
      expect(c.excluded.map((e) => e.ticker), isNot(contains('MSFT')));
      expect(c.excluded.map((e) => e.ticker), isNot(contains('META')));
    });
  });

  test('the multiples a figure used read as a list', () {
    expect(joinWithAnd([]), '');
    expect(joinWithAnd(['P/E']), 'P/E');
    expect(joinWithAnd(['P/E', 'Price/Book']), 'P/E and Price/Book');
    expect(
      joinWithAnd(['P/E', 'EV/EBITDA', 'Price/Sales']),
      'P/E, EV/EBITDA and Price/Sales',
    );
  });

  group('what cannot be added', () {
    test('the company itself', () async {
      final c = await opened(Backend(), 'AAPL');
      expect(
        c.addPeer(
          const CompanySearchResult(
            ticker: 'AAPL',
            name: 'Apple',
            exchange: '',
          ),
        ),
        contains('company being valued'),
      );
      expect(c.hasPendingChanges, isFalse);
    });

    test('a company already in the group', () async {
      final c = await opened(Backend(), 'JPM');
      expect(
        c.addPeer(
          const CompanySearchResult(ticker: 'BAC', name: 'BofA', exchange: ''),
        ),
        'BAC is already in the peer group.',
      );

      c.addPeer(company('MS'));
      expect(c.addPeer(company('MS')), 'MS is already in the peer group.');
    });

    test('more than the backend accepts', () async {
      final c = await opened(Backend(), 'AAPL');
      for (var i = 0; i < kMaxAddedPeers; i++) {
        expect(
          c.addPeer(
            CompanySearchResult(ticker: 'P$i', name: 'Peer $i', exchange: ''),
          ),
          isNull,
        );
      }
      expect(c.addPeer(company('MSFT')), 'At most 8 peers can be added.');
      expect(c.pending.added, hasLength(kMaxAddedPeers));
    });

    test('adding back a removed automatic peer restores it rather than duplicating it', () async {
      final c = await opened(Backend(), 'JPM');
      c.removePeer('WFC');

      expect(
        c.addPeer(
          const CompanySearchResult(
            ticker: 'WFC',
            name: 'Wells Fargo',
            exchange: '',
          ),
        ),
        isNull,
      );
      expect(c.hasPendingChanges, isFalse);
      expect(c.pending.added, isEmpty);
    });
  });

  group('when the source is throttled', () {
    test(
      'the previous figure stays, labelled, with the edits kept to retry',
      () async {
        final backend = Backend()..failRelativePosts = true;
        final c = await opened(backend, 'JPM');
        final automatic = figureOf(c)!;

        c.addPeer(company('MS'));
        await c.apply();

        expect(
          figureOf(c),
          automatic,
          reason: 'still the figure for the applied group',
        );
        expect(
          c.applyError,
          'Too many requests just now. Wait a moment and try again.',
        );
        expect(c.hasPendingChanges, isTrue, reason: 'nothing was lost');
        expect(c.applied.isEmpty, isTrue);

        backend.failRelativePosts = false;
        await c.apply();
        expect(c.applyError, isNull);
        expect(
          c.rows.any(
            (r) => r.ticker == 'MS' && r.state == PeerRowState.current,
          ),
          isTrue,
        );
      },
    );

    test('a failure is never cached as an answer', () async {
      final backend = Backend()..failRelativePosts = true;
      final c = await opened(backend, 'JPM');
      c.addPeer(company('MS'));
      await c.apply();
      final requests = backend.relativeRequests;

      backend.failRelativePosts = false;
      await c.apply();

      expect(backend.relativeRequests, requests + 1);
    });

    test('leaving the company while a request is in flight is safe', () async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'JPM');
      c.open();
      c.dispose();
      await pumpEventQueue(); // the response lands after disposal: no throw
    });
  });

  group('on the screen', () {
    Future<void> value(WidgetTester tester, String ticker) async {
      await tester.enterText(find.byType(TextField).first, ticker);
      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();
    }

    Future<void> openRelative(WidgetTester tester) async {
      await tester.tap(find.text('Relative (market)'));
      await tester.pump();
      await tester.pump();
    }

    Future<void> addViaSheet(WidgetTester tester, List<String> queries) async {
      final add = find.byKey(const Key('peer-add'));
      await tester.ensureVisible(add);
      await tester.tap(add);
      await tester.pumpAndSettle();
      for (final q in queries) {
        await tester.enterText(find.byKey(const Key('add-peer-field')), q);
        await tester.pump(const Duration(milliseconds: 300));
        await tester.pump();
        await tester.tap(
          find.descendant(
            of: find.byKey(const Key('add-peer-dropdown')),
            matching: find.text(_companies[q.toUpperCase()]!),
          ),
        );
        await tester.pump();
      }
      await tester.tap(find.byKey(const Key('add-peer-done')));
      await tester.pumpAndSettle();
    }

    Future<void> apply(WidgetTester tester) async {
      final button = find.byKey(const Key('peer-apply'));
      await tester.ensureVisible(button);
      await tester.tap(button);
      await tester.pump();
      await tester.pump();
    }

    testWidgets(
      'AAPL: add peers through search, apply, and a relative figure appears',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'AAPL');
        await openRelative(tester);
        expect(find.byKey(const Key('relative-declined')), findsOneWidget);
        final afterOpen = backend.relativeRequests;

        await addViaSheet(tester, ['msft', 'googl', 'meta']);

        expect(
          backend.relativeRequests,
          afterOpen,
          reason: 'adding is staged, not sent',
        );
        expect(find.byKey(const Key('peer-pending')), findsOneWidget);
        expect(find.byKey(const Key('relative-stale')), findsOneWidget);
        for (final t in ['MSFT', 'GOOGL', 'META']) {
          expect(find.textContaining('Will be added'), findsWidgets);
          expect(find.byKey(ValueKey('peer-row-$t')), findsOneWidget);
        }

        await apply(tester);

        expect(backend.relativeRequests, afterOpen + 1);
        expect(find.byKey(const Key('relative-figure')), findsOneWidget);
        expect(find.byKey(const Key('relative-declined')), findsNothing);
        expect(find.byKey(const Key('peer-pending')), findsNothing);
        expect(find.text('Edited by you'), findsOneWidget);
        expect(
          find.descendant(
            of: find.byKey(const ValueKey('peer-row-MSFT')),
            matching: find.text('Added by you'),
          ),
          findsOneWidget,
        );
      },
    );

    testWidgets('the sheet refuses a company already staged, in place', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);

      final add = find.byKey(const Key('peer-add'));
      await tester.ensureVisible(add);
      await tester.tap(add);
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('add-peer-field')), 'ms');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();
      await tester.tap(
        find.descendant(
          of: find.byKey(const Key('add-peer-dropdown')),
          matching: find.text('Morgan Stanley'),
        ),
      );
      await tester.pump();
      expect(find.byKey(const ValueKey('staged-MS')), findsOneWidget);

      await tester.enterText(find.byKey(const Key('add-peer-field')), 'ms');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();
      await tester.tap(
        find.descendant(
          of: find.byKey(const Key('add-peer-dropdown')),
          matching: find.text('Morgan Stanley'),
        ),
      );
      await tester.pump();

      expect(find.text('MS is already in the peer group.'), findsOneWidget);
    });

    testWidgets('removing a peer and applying updates the figure', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'AAPL');
      await openRelative(tester);
      await addViaSheet(tester, ['msft', 'googl', 'meta', 'amzn']);
      await apply(tester);
      final four = tester
          .widget<Text>(find.byKey(const Key('relative-figure')))
          .data;

      final remove = find.byKey(const ValueKey('peer-remove-AMZN'));
      await tester.ensureVisible(remove);
      await tester.tap(remove);
      await tester.pump();
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('peer-row-AMZN')),
          matching: find.textContaining('Will be removed'),
        ),
        findsOneWidget,
      );
      await apply(tester);

      final three = tester
          .widget<Text>(find.byKey(const Key('relative-figure')))
          .data;
      expect(three, isNot(four));
      expect(
        find.byKey(const ValueKey('peer-row-AMZN')),
        findsNothing,
        reason: 'a removed added peer simply leaves the group',
      );
    });

    testWidgets('JPM: reset returns to the automatic group', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);
      final automatic = tester
          .widget<Text>(find.byKey(const Key('relative-figure')))
          .data;

      final remove = find.byKey(const ValueKey('peer-remove-WFC'));
      await tester.ensureVisible(remove);
      await tester.tap(remove);
      await tester.pump();
      await apply(tester);
      expect(find.byKey(const Key('relative-declined')), findsOneWidget);
      expect(find.byKey(const ValueKey('peer-restore-WFC')), findsOneWidget);

      final reset = find.byKey(const Key('peer-reset'));
      await tester.ensureVisible(reset);
      await tester.tap(reset);
      await tester.pump();
      await tester.pump();

      expect(
        tester.widget<Text>(find.byKey(const Key('relative-figure'))).data,
        automatic,
      );
      expect(find.text('Chosen automatically'), findsWidgets);
      expect(find.byKey(const Key('peer-reset')), findsNothing);
    });

    testWidgets('editing peers leaves the intrinsic side exactly as it was', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      final sliders = Map.of(
        tester
            .widget<AssumptionSliders>(find.byType(AssumptionSliders))
            .current,
      );
      final valuations = backend.count('/valuation/JPM');

      await openRelative(tester);
      await addViaSheet(tester, ['ms']);
      await apply(tester);
      final intrinsicTab = find.text('Intrinsic value');
      await tester.ensureVisible(intrinsicTab);
      await tester.tap(intrinsicTab);
      await tester.pump();

      expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
      expect(
        tester
            .widget<AssumptionSliders>(find.byType(AssumptionSliders))
            .current,
        sliders,
      );
      expect(backend.count('/valuation/JPM'), valuations);
    });

    testWidgets(
      'closing the add-peer sheet does not hand focus back to the ticker field',
      (tester) async {
        // Found on the emulator: the keyboard popped up over the peer list.
        //
        // The path matters. Typing a ticker and pressing Value leaves the
        // field in the page's focus history - Value sits inside the field's
        // tap region, so tapping it is not "outside" - and closing a sheet
        // restores focus from that history. A tap elsewhere first would clear
        // the history and hide the bug, so the test must not make one.
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'AAPL');
        await openRelative(tester);

        await addViaSheet(tester, ['msft']);

        final ticker = tester.state<EditableTextState>(
          find.descendant(
            of: find.byType(TextField).first,
            matching: find.byType(EditableText),
          ),
        );
        expect(ticker.widget.focusNode.hasFocus, isFalse);
      },
    );

    testWidgets('"Add a peer" has room for its whole label', (tester) async {
      // Found on the emulator: the theme's outlined style pads vertically
      // only, and the label ran into the border.
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'AAPL');
      await openRelative(tester);

      final button = find.byKey(const Key('peer-add'));
      await tester.ensureVisible(button);
      final outer = tester.getRect(button);
      final label = tester.getRect(
        find.descendant(of: button, matching: find.text('Add a peer')),
      );

      expect(outer.right - label.right, greaterThanOrEqualTo(8));
    });

    testWidgets('a new company starts with its own automatic peers', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);
      await addViaSheet(tester, ['ms']);

      await value(tester, 'AAPL');
      await openRelative(tester);

      expect(find.byKey(const Key('peer-pending')), findsNothing);
      expect(find.byKey(const ValueKey('peer-row-MS')), findsNothing);
    });
  });
}
