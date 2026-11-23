/// The optional claim that the margin goes somewhere else.
///
/// The engine has taken `target_operating_margin` and
/// `margin_convergence_year` since the three-phase rebuild, the API accepts
/// them, the provenance explains them, and `kMarginTarget` named the two
/// controls - but nothing in the app ever drew them. The spec existed and
/// the screen did not.
///
/// Off by default, and that is the substance rather than a nicety: the
/// margin from the filings is the one figure in the model that came from
/// the company rather than from a judgement about the company. A target
/// sitting there pre-filled would invite a view the model has no basis for.
/// So the switch starts off, the margin holds flat until a reader says
/// otherwise, and switching it on seeds the target at the company's own
/// margin - opening the question without answering it.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

final _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

class Backend {
  /// Every valuation request, as url + body, newest last.
  final List<String> requests = [];

  String get last => requests.isEmpty ? '' : requests.last;

  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      final path = request.url.path;
      final isOverridden = path == '/valuation';
      if (isOverridden ||
          (path.startsWith('/valuation/') && !path.endsWith('/relative'))) {
        requests.add('${request.url}|${request.body}');
        final ticker = isOverridden
            ? (jsonDecode(request.body) as Map)['ticker'] as String
            : path.split('/')[2];
        final name = 'valuation_$ticker';
        if (_cases.containsKey(name)) {
          return http.Response(
            jsonEncode(_cases[name]['body']),
            _cases[name]['status_code'] as int,
            headers: const {'content-type': 'application/json'},
          );
        }
      }
      return http.Response('{"status":"ok","query":"","results":[]}', 200);
    }),
  );
}

final _header = find.byKey(const Key('advanced-header'));
final _toggle = find.byKey(const Key('margin-target-toggle'));

Future<void> valueTicker(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField), ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

Future<void> openAdvanced(WidgetTester tester) async {
  await tester.ensureVisible(_header);
  await tester.pump();
  await tester.tap(_header);
  await tester.pump();
  await tester.pump(AppMotion.enter + const Duration(milliseconds: 20));
}

Future<void> switchOn(WidgetTester tester) async {
  await tester.ensureVisible(_toggle);
  await tester.pump();
  await tester.tap(_toggle);
  await tester.pump();
  await tester.pump();
}

bool isOn(WidgetTester tester) => tester.widget<Switch>(_toggle).value;

void main() {
  testWidgets('the control is there, and off', (tester) async {
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));
    await valueTicker(tester, 'AAPL');
    await openAdvanced(tester);

    expect(_toggle, findsOneWidget);
    expect(isOn(tester), isFalse);
    expect(
      find.text('Where the margin ends up'),
      findsOneWidget,
      reason: 'the section has to say what it is for before it is switched on',
    );
  });

  testWidgets('off means the request carries no claim about the margin', (
    tester,
  ) async {
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));
    await valueTicker(tester, 'AAPL');
    await openAdvanced(tester);

    expect(backend.last, isNot(contains('target_operating_margin')));
    expect(backend.last, isNot(contains('margin_convergence_year')));
  });

  testWidgets('switching it on reveals both controls and asks the backend', (
    tester,
  ) async {
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));
    await valueTicker(tester, 'AAPL');
    await openAdvanced(tester);
    await switchOn(tester);

    expect(isOn(tester), isTrue);
    expect(find.text('Target operating margin'), findsOneWidget);
    expect(find.text('Reached by year'), findsOneWidget);
    expect(
      backend.last,
      contains('target_operating_margin'),
      reason: 'the figure above has to be the one the panel describes',
    );
    expect(backend.last, contains('margin_convergence_year'));
  });

  testWidgets('switching it on is not itself a claim', (tester) async {
    /// The target seeds at the company's own margin, so the first thing the
    /// reader sees is their own starting point. Seeded anywhere else - zero,
    /// a round number, an industry figure - and flicking the switch would
    /// move the valuation before anybody had said anything, which is the
    /// view the model has no basis for.
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));
    await valueTicker(tester, 'AAPL');
    await openAdvanced(tester);
    await switchOn(tester);

    final body = jsonDecode(backend.last.split('|').last) as Map;
    final sent = (body['overrides'] as Map)['target_operating_margin'];
    expect(
      (sent as num).toDouble(),
      closeTo(0.31510222870075566, 1e-9),
      reason: "the fixture's own operating margin, not a figure of ours",
    );
    expect(
      find.text('31.51%'),
      findsNWidgets(2),
      reason: "the company's own margin and the target now read the same, "
          'which is what makes switching it on a question rather than an '
          'answer',
    );
  });

  testWidgets('a fresh valuation puts it back to off', (tester) async {
    /// The same obligation the reinvestment method carries: a claim left
    /// switched on over a figure computed without it is the panel asserting
    /// something the number does not support.
    final backend = Backend();
    await tester.pumpWidget(DcfApp(api: backend.api()));
    await valueTicker(tester, 'AAPL');
    await openAdvanced(tester);
    await switchOn(tester);
    expect(isOn(tester), isTrue);

    await valueTicker(tester, 'CRM');

    expect(
      backend.last,
      isNot(contains('target_operating_margin')),
      reason: 'a fresh valuation does not carry the override',
    );
    await tester.ensureVisible(_toggle);
    await tester.pump();
    expect(
      isOn(tester),
      isFalse,
      reason: 'so the panel must not still claim a target margin',
    );
  });
}
