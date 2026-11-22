// A refusal with nothing after it.
//
// Tesla found this gap. It is profitable, so the speculative estimate is not
// offered for it, and it is refused a standard valuation because its
// investment outspends its profits - so the screen explained why the model
// would not value the company and stopped.
//
// These tests hold the pairing. The refusal must say, in plain language, why
// no figure is given and what the reader can do instead; the opt-in must be
// offered only where the backend will serve it; and tapping it must not be
// how the app finds out. Responses are the real backend's, recorded by
// tools/generate_speculative_fixtures.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/hypothetical_screen.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/speculative_screen.dart';
import 'package:dcf_app/valuation_api.dart';

final Map<String, dynamic> _refusals = jsonDecode(
  File('test/fixtures/speculative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

final Map<String, dynamic> _hypotheticals = jsonDecode(
  File('test/fixtures/hypothetical_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

/// A backend answering from the recordings, remembering every request.
class Backend {
  final List<http.Request> requests = [];

  List<String> get paths => requests.map((r) => r.url.path).toList();
  bool get askedForHypothetical =>
      paths.any((p) => p.endsWith('/hypothetical'));
  bool get askedForSpeculation => paths.any((p) => p.contains('speculative'));

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      requests.add(request);
      http.Response from(Map<String, dynamic> cases, String name) =>
          http.Response(
            jsonEncode(cases[name]['body']),
            cases[name]['status_code'] as int,
          );

      switch (request.url.path) {
        // Profitable, refused for cash flow: the hypothetical is the only
        // step left.
        case '/valuation/TSLA':
          return from(_refusals, 'tsla_refusal');
        // A lender: neither step is offered, because no assumption on that
        // screen repairs the shape of the model.
        case '/valuation/AXP':
          return from(_refusals, 'axp_no_steps');
        // A loss-maker: the speculative tier comes first.
        case '/valuation/RIVN':
          return from(_refusals, 'rivn_refusal');
        case '/valuation/TSLA/hypothetical':
          // On this route the screen opens on the company's own figures, so
          // the first request carries a real margin and gets a real answer.
          // A margin of nil is still refused, in this route's words.
          final margin =
              double.tryParse(
                request.url.queryParameters['target_operating_margin'] ?? '0',
              ) ??
              0;
          return from(
            _hypotheticals,
            margin <= 0 ? 'tsla_no_margin' : 'tsla_opening',
          );
        case '/search':
          return http.Response('{"query":"","results":[]}', 200);
      }
      return http.Response('{"status":"ok"}', 200);
    }),
  );
}

Future<void> valueTicker(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

final _speculativeOptIn = find.byKey(const Key('speculative-opt-in'));
final _hypotheticalOptIn = find.byKey(const Key('hypothetical-opt-in'));

void main() {
  group('the refusal explains itself', () {
    testWidgets('and says, in plain words, why no figure is given', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: Backend().api()));
      await valueTicker(tester, 'TSLA');

      expect(
        find.text('A standard valuation isn’t the right tool here'),
        findsOneWidget,
      );
      // The reason, in the words the backend wrote - no jargon, no code.
      expect(
        find.textContaining('spending more cash than it brings in'),
        findsOneWidget,
      );
      expect(
        find.textContaining('no positive cash flow to put a long-term value'),
        findsOneWidget,
      );
      // And not the loss-maker's reason, which is not true of this company.
      expect(find.textContaining('loss-making'), findsNothing);
    });

    testWidgets('once, not twice', (tester) async {
      await tester.pumpWidget(DcfApp(api: Backend().api()));
      await valueTicker(tester, 'TSLA');

      // The backend's message already says that no figure is given on
      // purpose, and says it with the company's name in it. A second line
      // underneath repeated it in weaker words, which read as insistence
      // rather than explanation.
      expect(find.textContaining('no economic meaning'), findsOneWidget);
      expect(
        find.textContaining('No intrinsic value is shown on purpose'),
        findsNothing,
      );
    });

    testWidgets('and the heading does not say DCF', (tester) async {
      await tester.pumpWidget(DcfApp(api: Backend().api()));
      await valueTicker(tester, 'TSLA');

      expect(
        find.text('A standard valuation isn’t the right tool here'),
        findsOneWidget,
      );
      expect(find.textContaining('A standard DCF'), findsNothing);
    });

    testWidgets('and points at the step that is left', (tester) async {
      await tester.pumpWidget(DcfApp(api: Backend().api()));
      await valueTicker(tester, 'TSLA');

      await tester.ensureVisible(_hypotheticalOptIn);
      expect(find.text('Set my own assumptions instead'), findsOneWidget);
      // Written for this route: the screen it opens starts from the
      // company's own figures, so the loss-maker's "nothing in it comes
      // from its accounts" would be false about the first thing shown.
      expect(
        find.textContaining(
          'what would have to be true about its growth, margins and '
          'investment',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining('nothing in it comes from its accounts'),
        findsNothing,
      );
      // The gentler step is not offered, because it is not on the table.
      expect(_speculativeOptIn, findsNothing);
    });

    testWidgets('without taking that step, or any other', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'TSLA');

      expect(
        backend.askedForHypothetical,
        isFalse,
        reason: 'offering it must not fetch it',
      );
      expect(backend.askedForSpeculation, isFalse);
      expect(find.byType(HypotheticalScreen), findsNothing);
    });
  });

  group('the opt-in', () {
    testWidgets('opens the hypothetical, and only on a tap', (tester) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'TSLA');
      expect(backend.askedForHypothetical, isFalse);

      await tester.ensureVisible(_hypotheticalOptIn);
      await tester.tap(_hypotheticalOptIn);
      await tester.pumpAndSettle();

      expect(find.byType(HypotheticalScreen), findsOneWidget);
      expect(backend.paths, contains('/valuation/TSLA/hypothetical'));
    });

    testWidgets(
      'starts from the backend’s values, never from any the app picked',
      (tester) async {
        final backend = Backend();
        await tester.pumpWidget(DcfApp(api: backend.api()));
        await valueTicker(tester, 'TSLA');
        await tester.ensureVisible(_hypotheticalOptIn);
        await tester.tap(_hypotheticalOptIn);
        await tester.pumpAndSettle();

        final sent = backend.requests
            .lastWhere((r) => r.url.path.endsWith('/hypothetical'))
            .url
            .queryParameters;
        final placeholders =
            _refusals['tsla_refusal']['body']['hypothetical_placeholders']
                as Map<String, dynamic>;
        expect(
          double.parse(sent['revenue_growth']!),
          placeholders['revenue_growth'],
        );
        expect(
          double.parse(sent['target_operating_margin']!),
          placeholders['target_operating_margin'],
        );
        // Not asserted to be any particular figure: whether they are the
        // loss-maker's neutral zeros or the company's own current rates is
        // the backend's decision, and the only thing the app must never do
        // is choose them. What it sends is exactly what it was handed.
        expect(
          double.parse(sent['years_to_target']!),
          placeholders['years_to_target'],
        );
      },
    );

    testWidgets('leaves the refusal underneath, so Back returns to it', (
      tester,
    ) async {
      await tester.pumpWidget(DcfApp(api: Backend().api()));
      await valueTicker(tester, 'TSLA');
      await tester.ensureVisible(_hypotheticalOptIn);
      await tester.tap(_hypotheticalOptIn);
      await tester.pumpAndSettle();

      // The screen's own back affordance, not tester.pageBack(), which looks
      // for a Cupertino button this app does not use.
      await tester.tap(
        find.widgetWithIcon(IconButton, Icons.arrow_back_rounded).first,
      );
      await tester.pumpAndSettle();

      expect(find.byType(HypotheticalScreen), findsNothing);
      expect(
        find.textContaining('spending more cash than it brings in'),
        findsOneWidget,
      );
    });
  });

  group('and is offered nowhere else', () {
    testWidgets('a lender gets neither step: no figure, and no door', (
      tester,
    ) async {
      final backend = Backend();
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'AXP');

      expect(
        find.text('A standard valuation isn’t the right tool here'),
        findsOneWidget,
      );
      expect(_hypotheticalOptIn, findsNothing);
      expect(_speculativeOptIn, findsNothing);
      expect(backend.askedForHypothetical, isFalse);
    });

    testWidgets(
      'a loss-maker is sent to the speculative tier first, not past it',
      (tester) async {
        await tester.pumpWidget(DcfApp(api: Backend().api()));
        await valueTicker(tester, 'RIVN');

        expect(_speculativeOptIn, findsOneWidget);
        expect(
          _hypotheticalOptIn,
          findsNothing,
          reason: 'the earlier step is still on the table',
        );
        expect(find.byType(SpeculativeScreen), findsNothing);
      },
    );
  });

  // -------------------------------------------------------------------------
  // The question the screen asks
  //
  // A loss-maker is assuming a turnaround its own figures point away from. A
  // profitable company that outspends its profits is not, and the loss-maker's
  // words are simply untrue about it. These hold that the second screen is
  // written for the company it is about.
  // -------------------------------------------------------------------------

  group('the screen behind the opt-in', () {
    Future<void> open(WidgetTester tester, Backend backend) async {
      await tester.pumpWidget(DcfApp(api: backend.api()));
      await valueTicker(tester, 'TSLA');
      await tester.ensureVisible(_hypotheticalOptIn);
      await tester.tap(_hypotheticalOptIn);
      await tester.pumpAndSettle();
    }

    testWidgets('opens on the company’s own figures, not on a prompt', (
      tester,
    ) async {
      final backend = Backend();
      await open(tester, backend);

      // A figure, immediately: the opening question on this route is "what
      // does today imply", and the backend answered it.
      expect(find.byKey(const Key('hypothetical-figure')), findsOneWidget);
      expect(find.text('No figure yet'), findsNothing);

      final sent = backend.requests
          .lastWhere((r) => r.url.path.endsWith('/hypothetical'))
          .url
          .queryParameters;
      expect(
        double.parse(sent['target_operating_margin']!),
        greaterThan(0),
        reason: 'the loss-maker’s neutral zero is not this route’s start',
      );
    });

    testWidgets('does not tell a profitable company its figures point the '
        'other way', (tester) async {
      await open(tester, Backend());

      expect(find.textContaining('point the other way'), findsNothing);
      expect(
        find.textContaining("start at Tesla, Inc's own current figures"),
        findsOneWidget,
      );
    });

    testWidgets('puts investment on a slider rather than taking it', (
      tester,
    ) async {
      await open(tester, Backend());

      // It used to be held fixed, and worse than fixed: the engine
      // substituted a 5% mature rate for whatever the company spends, which
      // for a company refused FOR spending past its profits was most of the
      // answer. The note explaining that is gone, because there is nothing
      // left to explain - it is a control like the others.
      expect(
        find.byKey(const Key('hypothetical-investment-note')),
        findsNothing,
      );
      final investment = find.text('Investment');
      await tester.ensureVisible(investment);
      expect(investment, findsOneWidget);
      expect(
        find.textContaining('starts at what the company spends today'),
        findsOneWidget,
      );
    });

    testWidgets('labels the sliders for this route, not the turnaround', (
      tester,
    ) async {
      await open(tester, Backend());

      expect(find.text('Operating margin'), findsWidgets);
      expect(find.text('Target operating margin'), findsNothing);
      expect(find.text('Years to reach that margin'), findsWidgets);
      expect(find.text('Years to the target'), findsNothing);
    });

    testWidgets('a margin of nil is refused in this route’s words', (
      tester,
    ) async {
      final backend = Backend();
      await open(tester, backend);

      final slider = find.byType(Slider);
      expect(slider, findsWidgets);
      // Driven through the API rather than the slider, because what is being
      // held is the wording the backend returns for this route.
      final refusal =
          _hypotheticals['tsla_no_margin']['body'] as Map<String, dynamic>;
      expect(refusal['message'] as String, isNot(contains('turnaround')));
      expect(
        refusal['message'] as String,
        isNot(contains('path to profitability')),
      );
      expect(
        refusal['message'] as String,
        contains('earning nothing on its sales'),
      );
    });
  });
}
