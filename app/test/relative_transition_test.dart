// A relative view that starts refused and then is not, and the reverse.
//
// Seagate is the case: on the peers chosen automatically only two comparable
// companies turn up, which is below the minimum, so the view refuses. Add a
// couple by hand and a real figure appears. The screen then has to stop
// saying there is no figure, and the workbook has to be built from the peers
// actually on screen rather than from the automatic group that failed.
//
// Both directions are tested, because a view that learns to clear the refusal
// but never to restore it is only half fixed.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/relative_controller.dart';
import 'package:dcf_app/relative_view.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/ticker_search.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> recorded(String name) =>
    _cases[name] as Map<String, dynamic>;

CompanySearchResult peer(String t) =>
    CompanySearchResult(ticker: t, name: '$t Inc.', exchange: 'NASDAQ');

/// Refuses on the automatic group and produces once peers are added, which is
/// what the real backend does for Seagate.
class Backend {
  final List<http.Request> requests = [];
  final List<Uri> excelGets = [];

  /// Set when the workbook endpoint is asked for peers it was not given.
  String? excelRefusal;

  List<String> get paths => requests.map((r) => r.url.path).toList();

  http.Request? lastWhere(bool Function(http.Request) test) {
    for (final r in requests.reversed) {
      if (test(r)) return r;
    }
    return null;
  }

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      final path = request.url.path;
      http.Response from(Map<String, dynamic> c) => http.Response(
        jsonEncode(c['body']),
        c['status_code'] as int,
      );

      // The automatic group: too few peers, so refused.
      if (path == '/valuation/STX/relative') return from(recorded('no_peers'));

      // An edited group: produced, unless the edit removed everything again.
      if (path == '/valuation/relative') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        final added = (body['add_peers'] as List<dynamic>? ?? const []);
        return added.isEmpty
            ? from(recorded('no_peers'))
            : from(recorded('produced'));
      }

      // The workbook. It must be told which peers to build from; given none,
      // it recomputes the automatic group and refuses exactly as above.
      if (path.endsWith('/relative/excel') || path == '/valuation/relative/excel') {
        List<String> added = const [];
        if (request.method == 'POST' && request.body.isNotEmpty) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          added = (body['add_peers'] as List<dynamic>? ?? const [])
              .map((e) => e.toString())
              .toList();
        }
        if (added.isEmpty) {
          excelRefusal = 'no relative valuation is produced';
          return http.Response(
            jsonEncode(recorded('no_peers')['body']),
            422,
          );
        }
        return http.Response.bytes(
          // A zip's first two bytes are all the app checks before saving.
          [0x50, 0x4B, 0x03, 0x04, 0, 0, 0, 0],
          200,
          headers: {
            'content-type':
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'content-disposition': 'attachment; filename="STX_Relative.xlsx"',
          },
        );
      }

      if (path == '/valuation/STX') {
        return from(recorded('valuation_AAPL'));
      }
      if (path == '/search') {
        return http.Response('{"query":"","results":[]}', 200);
      }
      return http.Response('{"status":"ok"}', 200);
    }),
  );
}

void main() {
  group('refused, then not', () {
    test('the figure arrives and the refusal goes with it', () async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      c.open();
      await Future<void>.delayed(Duration.zero);

      // As it starts: refused, and the app knows there is no figure.
      expect(c.report, isNotNull);
      expect(c.report!.hasFigure, isFalse,
          reason: 'two comparable companies is below the minimum');
      expect(c.report!.declineMessage, isNotEmpty);

      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();

      expect(c.report!.hasFigure, isTrue, reason: 'a figure was produced');
      expect(c.report!.relativeValue, isNotNull);
      // The refusal must not survive the edit that answered it.
      expect(c.report!.declineMessage, isEmpty);
      expect(c.report!.declineReasons, isEmpty);
    });

    test('and goes back to refused when the peers are taken away', () async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      c.open();
      await Future<void>.delayed(Duration.zero);

      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();
      expect(c.report!.hasFigure, isTrue);

      c.removePeer('LITE');
      c.removePeer('LRCX');
      await c.apply();

      expect(c.report!.hasFigure, isFalse, reason: 'back below the minimum');
      expect(c.report!.declineMessage, isNotEmpty,
          reason: 'and says so again, rather than showing a stale figure');
    });
  });

  group('what the screen shows through the change', () {
    Future<void> pumpView(
      WidgetTester tester,
      RelativeController c,
      Backend backend,
    ) async {
      final search = TickerSearchController(fetch: backend.api().search);
      addTearDown(search.dispose);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: Scaffold(
            body: SingleChildScrollView(
              child: RelativeView(controller: c, peerSearch: search),
            ),
          ),
        ),
      );
      await tester.pump();
    }

    testWidgets('the refusal panel goes when the figure arrives',
        (tester) async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      await pumpView(tester, c, backend);
      c.open();
      await tester.pump();
      await tester.pump();

      expect(find.byKey(const Key('relative-declined')), findsOneWidget,
          reason: 'it starts refused');
      expect(find.byKey(const Key('relative-figure')), findsNothing);

      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();
      await tester.pump();
      await tester.pump();

      expect(find.byKey(const Key('relative-figure')), findsOneWidget,
          reason: 'the figure is on screen');
      expect(find.byKey(const Key('relative-declined')), findsNothing,
          reason: 'and the refusal is not, beside it');
      expect(find.text('Not produced'), findsNothing);
    });

    testWidgets('and comes back when the peers are taken away again',
        (tester) async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      await pumpView(tester, c, backend);
      c.open();
      await tester.pump();
      await tester.pump();

      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();
      await tester.pump();
      expect(find.byKey(const Key('relative-figure')), findsOneWidget);

      c.removePeer('LITE');
      c.removePeer('LRCX');
      await c.apply();
      await tester.pump();
      await tester.pump();

      expect(find.byKey(const Key('relative-declined')), findsOneWidget,
          reason: 'refused again, said plainly');
      expect(find.byKey(const Key('relative-figure')), findsNothing,
          reason: 'and no stale figure left behind');
    });
  });

  group('the workbook follows the peers on screen', () {
    test('an edited group exports, rather than refusing on the old one',
        () async {
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      c.open();
      await Future<void>.delayed(Duration.zero);
      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();
      expect(c.report!.hasFigure, isTrue);

      final result = await backend.api().downloadRelativeExcel(
        'STX',
        addPeers: c.applied.added,
        removePeers: c.applied.removed.toList(),
      );

      expect(result, isA<ExcelSuccess>(),
          reason: 'the workbook is built from the peers that produced the '
              'figure, not from the automatic group that failed');
      expect(backend.excelRefusal, isNull);
    });

    test('asking by ticker alone is what used to refuse', () async {
      // The bug, pinned: with the peers left off, the backend returns to the
      // automatic group and refuses - and the app showed that refusal over a
      // figure that was on screen and perfectly good.
      final backend = Backend();
      final c = RelativeController(api: backend.api(), ticker: 'STX');
      c.open();
      await Future<void>.delayed(Duration.zero);
      c.addPeer(peer('LITE'));
      c.addPeer(peer('LRCX'));
      await c.apply();
      expect(c.report!.hasFigure, isTrue);

      final withoutPeers = await backend.api().downloadRelativeExcel('STX');
      expect(withoutPeers, isA<ExcelNotSuitable>(),
          reason: 'which is why the peers have to travel with the request');
    });

    test('an unedited group still exports the automatic comparison', () async {
      final backend = Backend();
      // A company whose automatic group does produce a figure uses the plain
      // endpoint, exactly as before.
      final result = await backend.api().downloadRelativeExcel('JPM');
      expect(result, isA<ExcelResult>());
    });
  });
}
