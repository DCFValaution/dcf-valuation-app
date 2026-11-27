/// The TRY chips, and the saved figure on a reopened save.
///
/// THE CHIPS
/// ---------
/// They were a Container with a rounded border and no gesture detector:
/// they sat under the word TRY, were pill-shaped, were the only thing on
/// the empty screen that resembled a control, and did nothing. On every
/// platform. Since they were written.
///
/// That is worse than a handler that broke, because a reader who taps one
/// and gets nothing concludes the app is broken rather than that the
/// thing was decoration. Found by driving the Android emulator and then
/// reproduced on web at both widths.
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

ValuationApi recordingApi(List<String> sent) => ValuationApi(
      baseUrl: 'http://test',
      client: MockClient((request) async {
        final path = request.url.path;
        sent.add('${request.url}|${request.body}');
        if (path == '/valuation' ||
            (path.startsWith('/valuation/') && !path.endsWith('/relative'))) {
          final ticker = path == '/valuation'
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

void main() {
  group('the TRY chips', () {
    testWidgets('every one of them is tappable', (tester) async {
      // The whole bug: these existed, looked like controls, and had no
      // gesture detector at all.
      await tester.pumpWidget(DcfApp(api: recordingApi([]), path: '/'));
      await tester.pump();

      for (final ticker in const ['AAPL', 'MSFT', 'NVDA', 'GOOGL']) {
        final chip = find.byKey(Key('try-$ticker'));
        expect(chip, findsOneWidget, reason: ticker);
        expect(
          tester.widget<InkWell>(find.descendant(
            of: chip,
            matching: find.byType(InkWell),
            matchRoot: true,
          )).onTap,
          isNotNull,
          reason: '$ticker has no handler, which is what shipped before',
        );
      }
    });

    testWidgets('tapping one values that company', (tester) async {
      final sent = <String>[];
      await tester.pumpWidget(DcfApp(api: recordingApi(sent), path: '/'));
      await tester.pump();

      await tester.ensureVisible(find.byKey(const Key('try-AAPL')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('try-AAPL')));
      await tester.pumpAndSettle();

      expect(sent.any((r) => r.contains('/valuation/AAPL')), isTrue,
          reason: 'the tap has to reach the backend: $sent');
    });

    testWidgets('the field shows what is being valued', (tester) async {
      // It goes through the same path as the button, so Back behaves the
      // same way and the screen does not claim to be valuing nothing.
      await tester.pumpWidget(DcfApp(api: recordingApi([]), path: '/'));
      await tester.pump();

      await tester.ensureVisible(find.byKey(const Key('try-MSFT')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('try-MSFT')));
      await tester.pumpAndSettle();

      expect(
        tester.widget<TextField>(find.byType(TextField).first).controller?.text,
        'MSFT',
      );
    });

    testWidgets('they are big enough to hit on a phone', (tester) async {
      // Previously sized only by their text, which on a 390px screen is
      // under the 44px a finger needs.
      await tester.pumpWidget(DcfApp(api: recordingApi([]), path: '/'));
      await tester.pump();

      final size = tester.getSize(find.byKey(const Key('try-NVDA')));
      expect(size.height, greaterThanOrEqualTo(44));
    });
  });

  group('the saved figure on a reopened save', () {
    Widget wrap({double? savedAt}) => MaterialApp(
          theme: buildDarkTheme(),
          home: Scaffold(
            body: BaselineDelta(
              baseline: 219.14,
              current: 103.05,
              savedAt: savedAt,
            ),
          ),
        );

    testWidgets('is shown beside the derived comparison', (tester) async {
      // Two different questions, and the reader came for the second:
      // "your assumptions against the model's", and "what has happened
      // since you saved this".
      await tester.pumpWidget(wrap(savedAt: 384.50));
      await tester.pumpAndSettle();

      expect(find.textContaining('vs derived', findRichText: true), findsOneWidget);
      expect(find.textContaining(r'saved at $384.50', findRichText: true), findsOneWidget);
    });

    testWidgets('is absent for an ordinary valuation', (tester) async {
      // An ordinary valuation has no day it was saved on, and inventing
      // one would be the screen asserting something untrue.
      await tester.pumpWidget(wrap());
      await tester.pumpAndSettle();

      expect(find.textContaining('vs derived', findRichText: true), findsOneWidget);
      expect(find.textContaining('saved at', findRichText: true), findsNothing);
    });
  });
}
