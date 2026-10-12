/// Every screen, at the width of a compact phone.
///
/// These are layout tests and nothing else: they assert that no RenderFlex
/// overflows and that no text is painted off the side of the screen. They say
/// nothing about what the numbers are, which is the business of the other
/// suites.
///
/// Both widths are checked deliberately. 360dp is the common compact Android
/// phone, and is where the overflows that prompted this were reported. 320dp
/// is the narrowest screen still in use, and a layout that only just survives
/// 360 usually fails below it - so the smaller width is the one that catches
/// fixed sizes hiding behind a bit of slack.
///
/// The responses are the recorded ones the other suites use, so these tests
/// exercise the real shapes: long company names, four-figure share prices,
/// multi-line notes and peer lists.

library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/advanced_panel.dart';
import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/hypothetical_screen.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/sensitivity_table.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';

import 'narrow_width.dart';

// --- the recordings -------------------------------------------------------

Map<String, dynamic> _load(String file) =>
    jsonDecode(File('test/fixtures/$file').readAsStringSync())
        as Map<String, dynamic>;

final _relative = _load('relative_cases.json');
final _speculative = _load('speculative_cases.json');
final _hypothetical = _load('hypothetical_cases.json');

http.Response _recorded(Map<String, dynamic> cases, String name) =>
    http.Response(
      jsonEncode(cases[name]['body']),
      cases[name]['status_code'] as int,
      headers: const {'content-type': 'application/json'},
    );

/// One backend answering for every screen, so a single harness can walk the
/// whole app rather than each suite rebuilding its own.
class Backend {
  ValuationApi api() => ValuationApi(
    baseUrl: 'http://test',
    client: MockClient((request) async {
      final path = request.url.path;

      if (path == '/search') {
        // A realistic dropdown: the longest names are the ones that overflow.
        return http.Response(
          jsonEncode({
            'query': 'a',
            'results': [
              {
                'ticker': 'AAPL',
                'name': 'Apple Inc.',
                'exchange': 'NASDAQ Global Select',
              },
              {
                'ticker': 'TSM',
                'name': 'Taiwan Semiconductor Manufacturing Company Limited',
                'exchange': 'NYSE',
              },
              {
                'ticker': 'BRK-B',
                'name': 'Berkshire Hathaway Inc. New Class B Common Stock',
                'exchange': 'NYSE',
              },
            ],
          }),
          200,
        );
      }

      if (path.endsWith('/hypothetical')) {
        final margin = double.tryParse(
              request.url.queryParameters['target_operating_margin'] ?? '0',
            ) ??
            0;
        return _recorded(
            _hypothetical, margin <= 0 ? 'intc_neutral' : 'intc_hypothetical');
      }

      switch (path) {
        case '/valuation/RIVN':
          return _recorded(_speculative, 'rivn_refusal');
        case '/valuation/RIVN/speculative':
          return _recorded(_speculative, 'rivn_speculative');
        case '/valuation/JPM':
          return _recorded(_relative, 'valuation_JPM');
        case '/valuation/AAPL':
          return _recorded(_relative, 'valuation_AAPL');
        case '/valuation/CRM':
          return _recorded(_relative, 'valuation_CRM');
        case '/valuation/JPM/relative':
          return _recorded(_relative, 'produced');
        case '/valuation/AAPL/relative':
          return _recorded(_relative, 'no_peers');
        case '/valuation/CRM/relative':
          return _recorded(_relative, 'single_multiple');
        case '/valuation/relative':
          return _recorded(_relative, 'edited');
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

Future<void> openRelative(WidgetTester tester) async {
  await tester.tap(find.text('Relative (market)'));
  await tester.pump();
  await tester.pump();
}

/// Asserts a screen fits, reporting every overflow and every clipped label.
///
/// [requires] guards against the test passing for the wrong reason. A screen
/// that failed to build overflows nothing at all, so each case names a piece
/// of itself that has to be on screen for the measurement to mean anything.
Future<void> expectFits(
  WidgetTester tester,
  double width,
  String what,
  Future<void> Function() build, {
  List<Finder> requires = const [],
}) async {
  late List<Finder> missing;
  final found = await overflowsAt(tester, width, () async {
    await build();
    missing = requires.where((f) => f.evaluate().isEmpty).toList();
  });
  expect(missing, isEmpty,
      reason: '$what did not render: ${missing.join(', ')}');
  expectNoOverflow(found, what, width);
}

void main() {
  // Each screen is checked at both widths. The loop keeps the two from
  // drifting apart, which is how a 320dp regression would otherwise slip in.
  for (final width in const [kNarrowWidth, kVeryNarrowWidth]) {
    final at = '${width.toStringAsFixed(0)}dp';

    group('the home screen at $at', () {
      testWidgets('search, empty', (tester) async {
        await expectFits(tester, width, 'The home screen', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await tester.pump();
        });
      });

      testWidgets('the suggestions dropdown, with long company names',
          (tester) async {
        await expectFits(tester, width, 'The search dropdown', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await tester.enterText(find.byType(TextField), 'a');
          await tester.pump(const Duration(milliseconds: 400));
          await tester.pump();
        });
      });
    });

    group('the DCF result at $at', () {
      testWidgets('headline, ROIC line, note, sliders, sensitivity, export',
          (tester) async {
        await expectFits(
          tester,
          width,
          'The DCF result',
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'AAPL');
          },
          requires: [
            find.text('INTRINSIC VALUE PER SHARE'),
            find.byType(AssumptionSliders),
            find.byType(SensitivityTable),
          ],
        );
      });

      testWidgets('scrolled to the bottom, where the table and export sit',
          (tester) async {
        await expectFits(tester, width, 'The DCF result, scrolled', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'AAPL');
          await tester.drag(
              find.byType(Scrollable).first, const Offset(0, -4000));
          await tester.pump();
        });
      });
    });

    group('the DDM result at $at', () {
      testWidgets('method badge, dividend basis, buyback warning',
          (tester) async {
        await expectFits(
          tester,
          width,
          'The DDM result',
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'JPM');
          },
          requires: [
            // The badge naming the model, and the buyback warning this
            // recording carries - the widest piece of prose on the screen.
            find.textContaining('Dividend Discount'),
            find.textContaining('share buybacks'),
          ],
        );
      });
    });

    group('the Advanced section at $at', () {
      testWidgets('open, fade on, with a full ten-year growth path',
          (tester) async {
        // Ten distinct rates is the widest the path can get, and the case the
        // projection-length slider made reachable.
        await expectFits(tester, width, 'The Advanced section', () async {
          await tester.pumpWidget(
            MaterialApp(
              theme: buildLightTheme(),
              home: Scaffold(
                body: SingleChildScrollView(
                  child: AdvancedPanel(
                    expanded: true,
                    onExpandedChanged: (_) {},
                    fadeEnabled: true,
                    onFadeChanged: (_) {},
                    fadeStartYear: 3,
                    onStartYearChanged: (_) {},
                    fadePattern: 'linear',
                    onPatternChanged: (_) {},
                    projectionYears: 10,
                    growthPath: const [
                      0.185, 0.185, 0.185, 0.164, 0.142, 0.121,
                      0.099, 0.078, 0.056, 0.025,
                    ],
                  ),
                ),
              ),
            ),
          );
          await tester.pump();
        });
      });
    });

    group('the speculative screen at $at', () {
      testWidgets('the refusal and its opt-in', (tester) async {
        await expectFits(tester, width, 'The speculative refusal', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'RIVN');
        });
      });

      testWidgets('the speculative figure and its banners', (tester) async {
        await expectFits(tester, width, 'The speculative result', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'RIVN');
          final optIn = find.byKey(const Key('speculative-opt-in'));
          if (optIn.evaluate().isNotEmpty) {
            await tester.ensureVisible(optIn);
            await tester.pump();
            await tester.tap(optIn);
            await tester.pump();
            await tester.pump();
          }
        });
      });
    });

    group('the hypothetical screen at $at', () {
      testWidgets('banners, the contradiction table and the figure',
          (tester) async {
        final placeholders = _hypothetical['intc_speculative_refused']['body']
            ['hypothetical_placeholders'] as Map<String, dynamic>;
        await expectFits(tester, width, 'The hypothetical screen', () async {
          await tester.pumpWidget(
            MaterialApp(
              theme: buildLightTheme(),
              home: HypotheticalScreen(
                api: Backend().api(),
                ticker: 'INTC',
                companyName: 'Intel Corporation',
                placeholders: HypotheticalInputs(
                  revenueGrowth:
                      (placeholders['revenue_growth'] as num).toDouble(),
                  targetOperatingMargin:
                      (placeholders['target_operating_margin'] as num)
                          .toDouble(),
                  yearsToTarget:
                      (placeholders['years_to_target'] as num).round(),
                ),
              ),
            ),
          );
          await tester.pump();
          await tester.pump();
        });
      });
    });

    group('the relative view at $at', () {
      testWidgets('the tab switcher labels fit', (tester) async {
        await expectFits(tester, width, 'The lens switcher', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'JPM');
        });
      });

      testWidgets('produced: figure, multiples table, peer list',
          (tester) async {
        await expectFits(
          tester,
          width,
          'The relative view',
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'JPM');
            await openRelative(tester);
          },
          requires: [
            find.byKey(const Key('relative-figure')),
            find.byKey(const Key('relative-peers')),
          ],
        );
      });

      testWidgets('produced and scrolled through the peers', (tester) async {
        await expectFits(tester, width, 'The relative view, scrolled',
            () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'JPM');
          await openRelative(tester);
          await tester.drag(
              find.byType(Scrollable).first, const Offset(0, -4000));
          await tester.pump();
        });
      });

      testWidgets('declined for too few peers', (tester) async {
        await expectFits(
          tester,
          width,
          'The relative decline',
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'AAPL');
            await openRelative(tester);
          },
          requires: [find.byKey(const Key('relative-declined'))],
        );
      });

      testWidgets('declined for a single multiple', (tester) async {
        await expectFits(tester, width, 'The single-multiple decline',
            () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'CRM');
          await openRelative(tester);
        });
      });

      testWidgets('the add-peer sheet', (tester) async {
        await expectFits(tester, width, 'The add-peer sheet', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'JPM');
          await openRelative(tester);
          final add = find.byKey(const Key('peer-add'));
          await tester.ensureVisible(add);
          await tester.pump();
          await tester.tap(add);
          await tester.pump();
          await tester.pump();
        });
      });

      testWidgets('a peer removed, with the pending-change bar', (tester) async {
        // The row's own controls plus an "edited by you" provenance label is
        // the widest a peer row gets.
        await expectFits(tester, width, 'The edited peer list', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'JPM');
          await openRelative(tester);
          final remove = find.byWidgetPredicate(
            (w) =>
                w.key is ValueKey &&
                '${(w.key as ValueKey).value}'.startsWith('peer-remove-'),
          );
          if (remove.evaluate().isNotEmpty) {
            await tester.ensureVisible(remove.first);
            await tester.pump();
            await tester.tap(remove.first);
            await tester.pump();
            await tester.pump();
          }
        });
      });
    });

    group('the sliders at $at', () {
      testWidgets('the tap-to-type editor for an assumption', (tester) async {
        await expectFits(tester, width, 'The exact-value editor', () async {
          await tester.pumpWidget(DcfApp(api: Backend().api()));
          await valueTicker(tester, 'AAPL');
          final exact = find.byKey(const ValueKey('exact-wacc'));
          if (exact.evaluate().isNotEmpty) {
            await tester.ensureVisible(exact);
            await tester.pump();
            await tester.tap(exact);
            await tester.pump();
            await tester.pump();
          }
        });
      });
    });
  }

  // Larger system text, at the narrow width, is the combination that actually
  // reaches users: Android's font-size setting goes well past this, and a
  // layout with no give left turns a preference into a broken screen. These
  // are the cases the inflexible rows in the chrome used to fail.
  group('with larger system text at 360dp', () {
    for (final scale in const [1.3, 1.6]) {
      testWidgets('the DCF result at ${scale}x', (tester) async {
        final found = await overflowsAt(
          tester,
          kNarrowWidth,
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'AAPL');
          },
          textScale: scale,
        );
        expectNoOverflow(found, 'The DCF result at ${scale}x text',
            kNarrowWidth);
      });

      testWidgets('the relative view at ${scale}x', (tester) async {
        final found = await overflowsAt(
          tester,
          kNarrowWidth,
          () async {
            await tester.pumpWidget(DcfApp(api: Backend().api()));
            await valueTicker(tester, 'JPM');
            await openRelative(tester);
          },
          textScale: scale,
        );
        expectNoOverflow(found, 'The relative view at ${scale}x text',
            kNarrowWidth);
      });
    }
  });

  // The method badge is the longest pill in the app and the one a reader is
  // most likely to be looking at. Its text is themed, so it is measured in
  // the real font here - unlike the tab labels, whose style carries no font
  // family and so cannot be judged from a test at all.
  group('the method badge reads as a badge, not a paragraph', () {
    testWidgets('it wraps at most once and is never cut short', (tester) async {
      await overflowsAt(tester, kNarrowWidth, () async {
        await tester.pumpWidget(DcfApp(api: Backend().api()));
        await valueTicker(tester, 'JPM');

        const label = 'Valued with a Dividend Discount Model';
        expect(find.text(label), findsOneWidget);
        expect(isTruncated(tester, label), isFalse,
            reason: 'the badge was clipped rather than wrapped');
        expect(lineCount(tester, label), lessThanOrEqualTo(2),
            reason: 'the badge broke into more lines than a badge should');
      });
    });
  });

  // Clipping is a separate failure from overflow: a label inside a scrolling
  // row can be painted past the edge without any Flex complaining.
  group('nothing is painted off the side of the screen', () {
    testWidgets('the relative view at 360dp', (tester) async {
      final clipped = <String>[];
      await overflowsAt(tester, kNarrowWidth, () async {
        await tester.pumpWidget(DcfApp(api: Backend().api()));
        await valueTicker(tester, 'JPM');
        await openRelative(tester);
        clipped.addAll(clippedHorizontally(tester, kNarrowWidth));
      });
      expect(clipped, isEmpty,
          reason: 'painted outside a ${kNarrowWidth}dp screen:\n'
              '${clipped.join('\n')}');
    });

    testWidgets('the DCF result at 360dp', (tester) async {
      final clipped = <String>[];
      await overflowsAt(tester, kNarrowWidth, () async {
        await tester.pumpWidget(DcfApp(api: Backend().api()));
        await valueTicker(tester, 'AAPL');
        clipped.addAll(clippedHorizontally(tester, kNarrowWidth));
      });
      expect(clipped, isEmpty,
          reason: 'painted outside a ${kNarrowWidth}dp screen:\n'
              '${clipped.join('\n')}');
    });
  });
}
