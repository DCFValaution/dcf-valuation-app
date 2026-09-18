// The data provider is not named in anything a person reads.
//
// Not a secret: the code and its comments still say what the backend talks to,
// and the server's own diagnostics report on it by name. But a message on
// screen should say what went wrong rather than advertise whose API it came
// from, and "the market data provider" is what a reader needs anyway.
//
// Two sweeps, because the text reaches a screen by two routes:
//
//   * the backend's, through the recorded responses the app renders. Those
//     fixtures are real API output, so a message that regressed on the server
//     would show up here;
//   * the app's own, through string literals in lib/.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

final _forbidden = RegExp(r'yahoo|yfinance', caseSensitive: false);

/// A line's code with any `//` comment removed.
///
/// Comments are documentation for whoever maintains this and are left alone;
/// only what could be rendered is checked.
String _withoutComment(String line) {
  final trimmed = line.trimLeft();
  if (trimmed.startsWith('//')) return '';
  final marker = line.indexOf('//');
  // Naive, and deliberately so: cutting at the first // can only ever check
  // more text than it should, never less.
  return marker == -1 ? line : line.substring(0, marker);
}

void main() {
  group('the recorded backend responses', () {
    final fixtures = Directory('test/fixtures')
        .listSync()
        .whereType<File>()
        .where((f) => f.path.endsWith('.json'))
        .toList();

    test('there are fixtures to check', () {
      expect(fixtures, isNotEmpty);
    });

    for (final file in fixtures) {
      final name = file.uri.pathSegments.last;
      test('$name names no provider', () {
        final content = file.readAsStringSync();
        final match = _forbidden.firstMatch(content);

        expect(
          match,
          isNull,
          reason:
              '$name contains "${match?.group(0)}" — re-record it with '
              'tools/generate_*_fixtures.py against the current backend',
        );
      });
    }
  });

  group('the app\'s own strings', () {
    test('no rendered text in lib/ names the provider', () {
      final offenders = <String>[];

      for (final file in Directory('lib')
          .listSync(recursive: true)
          .whereType<File>()
          .where((f) => f.path.endsWith('.dart'))) {
        final lines = file.readAsLinesSync();
        for (var i = 0; i < lines.length; i++) {
          final code = _withoutComment(lines[i]);
          if (_forbidden.hasMatch(code)) {
            offenders.add('${file.path}:${i + 1}: ${lines[i].trim()}');
          }
        }
      }

      expect(
        offenders,
        isEmpty,
        reason: 'the provider is named in code that could be shown:\n'
            '${offenders.join('\n')}',
      );
    });
  });
}
