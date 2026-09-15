// The relative (peer-multiple) view.
//
// Its promises are mostly about framing and about leaving the rest alone:
// never labelled intrinsic, always shown beside the intrinsic value, declines
// presented as answers rather than errors - and opening it must not change,
// refetch or reset anything on the intrinsic side. Responses are the real
// backend's, recorded by tools/generate_relative_fixtures.py.

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
import 'package:dcf_app/ticker_search.dart';
import 'package:dcf_app/sensitivity_table.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> body(String name) =>
    _cases[name]['body'] as Map<String, dynamic>;
int statusOf(String name) => _cases[name]['status_code'] as int;

String money(double v) => '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';

class Backend {
  final List<http.Request> requests = [];
  int relativeStatus = 0; // 0: answer from the recordings

  List<String> get paths => requests.map((r) => r.url.path).toList();
  int count(String path) => paths.where((p) => p == path).length;

  static const _relativeCase = {
    'JPM': 'produced',
    'AAPL': 'no_peers',
    'CRM': 'single_multiple',
    'RIVN': 'not_applicable',
  };

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      final path = request.url.path;
      final match = RegExp(r'^/valuation/([A-Z]+)(/relative)?$')
          .firstMatch(path);
      if (match != null) {
        final ticker = match.group(1)!;
        if (match.group(2) != null) {
          if (relativeStatus != 0) {
            return http.Response(
              '{"code":"upstream_error","message":"Yahoo is unavailable"}',
              relativeStatus,
            );
          }
          final name = _relativeCase[ticker]!;
          return http.Response(jsonEncode(body(name)), statusOf(name));
        }
        final name = 'valuation_$ticker';
        return http.Response(jsonEncode(body(name)), statusOf(name));
      }
      if (path == '/valuation') {
        final ticker = (jsonDecode(request.body) as Map)['ticker'];
        return http.Response(jsonEncode(body('valuation_$ticker')), 200);
      }
      if (path == '/search') {
        return http.Response('{"query":"","results":[]}', 200);
      }
      return http.Response('{"status":"ok"}', 200);
    }),
  );
}

Future<void> value(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

final _relativeTab = find.text('Relative (market)');
final _intrinsicTab = find.text('Intrinsic value');

Future<void> openRelative(WidgetTester tester) async {
  await tester.tap(_relativeTab);
  await tester.pump();
  await tester.pump();
}

void main() {
  group('reading the responses', () {
    test('a produced relative view: figure, peers, multiples, framing', () {
      final r = RelativeReport.produced(body('produced'));

      expect(r.hasFigure, isTrue);
      expect(r.low! <= r.relativeValue! && r.relativeValue! <= r.high!, isTrue);
      expect(r.multiplesApplied, isNotEmpty);
      expect(r.peerSelection!.isAutomatic, isTrue);
      expect(r.peerSelection!.peers.length, greaterThanOrEqualTo(3));
      expect(
        r.peerSelection!.peers.every(
          (p) => p.name.isNotEmpty && p.ticker.isNotEmpty,
        ),
        isTrue,
      );
      expect(
        r.peerSelection!.peers.first.provenanceLabel,
        'Chosen automatically',
      );
      expect(r.note, contains('mispricing'));
      expect(r.comparisonStatement, contains('information, not an error'));
      expect(r.intrinsic!.method, ValuationMethod.ddm);
    });

    test('a decline for lack of peers keeps the intrinsic value', () {
      final r = RelativeReport.declined(body('no_peers'));

      expect(r.hasFigure, isFalse);
      expect(r.peerSelection!.peers, isEmpty);
      expect(r.peerSelection!.excluded, isNotEmpty);
      expect(r.intrinsic, isNotNull);
      expect(r.declineReasons.single, contains('No comparable company'));
    });

    test(
      'a single-multiple decline keeps the multiples, with no implied value',
      () {
        final r = RelativeReport.declined(body('single_multiple'));

        expect(r.hasFigure, isFalse);
        expect(r.multiples.where((m) => m.applicable), hasLength(1));
        expect(
          r.multiples.every((m) => m.impliedValuePerShare == null),
          isTrue,
        );
        expect(r.declineReasons.single, contains('single multiple'));
      },
    );

    test('each outcome is told apart by status and code', () async {
      final backend = Backend();
      final api = backend.api();

      expect(await api.relative('JPM'), isA<RelativeAvailable>());
      expect(await api.relative('AAPL'), isA<RelativeAvailable>());
      expect(await api.relative('RIVN'), isA<RelativeNotApplicable>());

      backend.relativeStatus = 502;
      final failed = await api.relative('JPM');
      expect(failed, isA<RelativeFailure>());
      // The data provider's own explanation, after a prefix that is true
      // whether it was unreachable or sent back incomplete data.
      expect(
        (failed as RelativeFailure).message,
        '$kDataUnavailablePrefix\n\nYahoo is unavailable',
      );
    });
  });

  group('the intrinsic side is untouched', () {
    testWidgets(
      'valuing opens on the intrinsic value, and nothing relative is fetched',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');

        expect(find.byKey(const Key('lens-switcher')), findsOneWidget);
        expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
        expect(find.byType(AssumptionSliders), findsOneWidget);
        expect(find.byType(SensitivityTable), findsOneWidget);
        expect(find.byType(RelativeView), findsNothing);
        expect(backend.count('/valuation/JPM/relative'), 0);
      },
    );

    testWidgets(
      'switching back restores it exactly, sliders included, with no refetch',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');

        // Move a slider first, so there is state to lose.
        tester
            .widget<AssumptionSliders>(find.byType(AssumptionSliders))
            .onChanged('cost_of_equity', 0.103);
        await tester.pump();
        final before = Map.of(
          tester
              .widget<AssumptionSliders>(find.byType(AssumptionSliders))
              .current,
        );
        final valuationsBefore = backend.count('/valuation/JPM');

        await openRelative(tester);
        expect(find.text('INTRINSIC VALUE PER SHARE'), findsNothing);
        await tester.tap(_intrinsicTab);
        await tester.pump();

        expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
        expect(
          tester
              .widget<AssumptionSliders>(find.byType(AssumptionSliders))
              .current,
          before,
        );
        expect(backend.count('/valuation/JPM'), valuationsBefore);

        await openRelative(tester);
        expect(
          backend.count('/valuation/JPM/relative'),
          1,
          reason: 'opened twice, fetched once',
        );
      },
    );

    testWidgets('a refusal offers no relative view at all', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'RIVN');

      expect(
        find.text('A standard DCF isn’t the right tool here'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('lens-switcher')), findsNothing);
      expect(backend.count('/valuation/RIVN/relative'), 0);
    });

    testWidgets(
      'a new search returns to the intrinsic lens and drops the old view',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');
        await openRelative(tester);

        await value(tester, 'AAPL');

        expect(find.text('INTRINSIC VALUE PER SHARE'), findsOneWidget);
        expect(find.byType(RelativeView), findsNothing);
        await openRelative(tester);
        expect(
          find.textContaining('Citigroup'),
          findsNothing,
          reason: "JPM's peers are gone",
        );
      },
    );
  });

  group('JPM: automatic peers', () {
    testWidgets('shows a relative value, never labelled intrinsic', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);

      final rv = body('produced')['relative_valuation'] as Map<String, dynamic>;
      final central = (rv['relative_value_per_share']['central'] as num)
          .toDouble();

      expect(backend.count('/valuation/JPM/relative'), 1);
      expect(find.text('RELATIVE VALUE PER SHARE'), findsOneWidget);
      expect(
        tester.widget<Text>(find.byKey(const Key('relative-figure'))).data,
        money(central),
      );
      expect(find.text('RELATIVE · MARKET-BASED'), findsOneWidget);
      expect(
        find.textContaining('This is not the intrinsic value'),
        findsOneWidget,
      );
      expect(find.text('INTRINSIC VALUE PER SHARE'), findsNothing);
    });

    testWidgets(
      'sets the intrinsic value beside it, with the backend\'s statement',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');
        await openRelative(tester);

        final intrinsic =
            (body(
                      'produced',
                    )['intrinsic_valuation']['intrinsic_value_per_share']
                    as num)
                .toDouble();
        expect(find.text('Intrinsic value (DDM)'), findsOneWidget);
        expect(
          tester.widget<Text>(find.byKey(const Key('relative-intrinsic'))).data,
          money(intrinsic),
        );
        expect(
          find.textContaining('information, not an error'),
          findsOneWidget,
        );
        expect(find.textContaining('mispricing'), findsOneWidget);
      },
    );

    testWidgets('lists every peer by name and ticker, with provenance', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);

      final peers =
          (body('produced')['relative_valuation']['peer_selection']['peers']
                  as List)
              .cast<Map<String, dynamic>>();
      final group = find.byKey(const Key('relative-peers'));
      for (final p in peers) {
        expect(
          find.descendant(of: group, matching: find.text(p['name'] as String)),
          findsOneWidget,
        );
        expect(
          find.descendant(
            of: group,
            matching: find.text(p['ticker'] as String),
          ),
          findsOneWidget,
        );
      }
      expect(
        find.descendant(of: group, matching: find.text('Chosen automatically')),
        findsWidgets,
      );
    });

    testWidgets(
      'shows each multiple with the company, the peer median and the position',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');
        await openRelative(tester);

        final multiples =
            (body('produced')['relative_valuation']['multiples'] as List)
                .cast<Map<String, dynamic>>();
        for (final m in multiples) {
          final card = find.byKey(ValueKey('multiple-${m['name']}'));
          expect(card, findsOneWidget);
          expect(
            find.descendant(
              of: card,
              matching: find.text(m['label'] as String),
            ),
            findsOneWidget,
          );
          expect(
            find.descendant(of: card, matching: find.text('Peer median')),
            findsOneWidget,
          );
          expect(
            find.descendant(
              of: card,
              matching: find.textContaining('the peer median'),
            ),
            findsOneWidget,
          );
        }
      },
    );

    testWidgets(
      'says the comparison uses derived assumptions when sliders are moved',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'JPM');
        tester
            .widget<AssumptionSliders>(find.byType(AssumptionSliders))
            .onChanged('cost_of_equity', 0.103);
        await tester.pump();

        await openRelative(tester);

        expect(
          find.textContaining('uses the derived assumptions'),
          findsOneWidget,
        );
      },
    );
  });

  group('declines are answers, not errors', () {
    testWidgets(
      'AAPL: no comparable peers, and the intrinsic value still shown',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'AAPL');
        await openRelative(tester);

        final intrinsic =
            (body(
                      'no_peers',
                    )['intrinsic_valuation']['intrinsic_value_per_share']
                    as num)
                .toDouble();
        expect(find.byKey(const Key('relative-declined')), findsOneWidget);
        expect(
          find.text('No relative figure for this company'),
          findsOneWidget,
        );
        expect(
          find.textContaining('No comparable companies were found.'),
          findsOneWidget,
        );
        expect(find.byKey(const Key('relative-figure')), findsNothing);
        expect(
          tester.widget<Text>(find.byKey(const Key('relative-intrinsic'))).data,
          money(intrinsic),
        );
        expect(find.text('Not produced'), findsOneWidget);
        expect(find.textContaining('could not be loaded'), findsNothing);
        expect(find.text('Try again'), findsNothing);
      },
    );

    testWidgets(
      'CRM: the multiples shown as information, the figure withheld',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await value(tester, 'CRM');
        await openRelative(tester);

        expect(find.byKey(const Key('relative-declined')), findsOneWidget);
        expect(find.textContaining('single multiple'), findsOneWidget);
        expect(
          find.textContaining('Shown as information only'),
          findsOneWidget,
        );
        expect(find.byKey(const ValueKey('multiple-ps')), findsOneWidget);
        expect(
          find.descendant(
            of: find.byKey(const ValueKey('multiple-ps')),
            matching: find.text('Withheld'),
          ),
          findsOneWidget,
        );
        expect(
          find.descendant(
            of: find.byKey(const ValueKey('multiple-pe')),
            matching: find.text('Not applied'),
          ),
          findsOneWidget,
        );
        expect(find.byKey(const Key('relative-figure')), findsNothing);
        expect(find.byKey(const Key('relative-intrinsic')), findsOneWidget);
      },
    );

    testWidgets('not applicable is explained, not failed', (tester) async {
      final api = Backend().api();
      final controller = RelativeController(api: api, ticker: 'RIVN');
      final search = TickerSearchController(fetch: api.search);
      addTearDown(controller.dispose);
      addTearDown(search.dispose);

      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: Scaffold(
            body: SingleChildScrollView(
              child: RelativeView(controller: controller, peerSearch: search),
            ),
          ),
        ),
      );
      controller.open();
      await tester.pump();
      await tester.pump();

      expect(find.text('No relative view'), findsOneWidget);
      expect(
        find.textContaining('second opinion, never a substitute'),
        findsOneWidget,
      );
      expect(find.text('Try again'), findsNothing);
    });

    testWidgets('a real failure is shown as one, and can be retried', (
      tester,
    ) async {
      final backend = Backend()..relativeStatus = 502;
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await value(tester, 'JPM');
      await openRelative(tester);

      expect(
        find.text('The relative view could not be loaded'),
        findsOneWidget,
      );
      backend.relativeStatus = 0;
      await tester.tap(find.text('Try again'));
      await tester.pump();
      await tester.pump();

      expect(find.text('RELATIVE VALUE PER SHARE'), findsOneWidget);
      expect(backend.count('/valuation/JPM/relative'), 2);
    });
  });
}
