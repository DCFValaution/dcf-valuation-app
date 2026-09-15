// Tests for reflowing backend messages written for a terminal.

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/formatting.dart';

void main() {
  group('tidyBackendMessage', () {
    // A message in the backend's terminal style - hard-wrapped, with indented
    // continuation lines - which would otherwise render with stray
    // indentation mid-paragraph.
    const wrapped =
        "Yahoo Finance recognises 'AAPL' but returned no data for it just now.\n"
        '  This is a temporary problem on the data provider\'s side, not an '
        'unknown ticker.\n'
        '  Try again shortly.';

    test('joins hard-wrapped lines and strips terminal indentation', () {
      final tidied = tidyBackendMessage(wrapped);

      expect(tidied.contains('\n'), isFalse);
      expect(tidied.contains('  '), isFalse, reason: 'no double spaces left');
      expect(tidied, startsWith("Yahoo Finance recognises 'AAPL'"));
      expect(tidied, contains('just now. This is a temporary problem'));
    });

    test('preserves genuine paragraph breaks', () {
      const input = 'First paragraph line one\nline two\n\nSecond paragraph';
      expect(
        tidyBackendMessage(input),
        'First paragraph line one line two\n\nSecond paragraph',
      );
    });

    test('leaves an already-clean message untouched', () {
      const clean = 'A standard DCF is not suitable for this company.';
      expect(tidyBackendMessage(clean), clean);
    });

    test('handles empty and whitespace-only input', () {
      expect(tidyBackendMessage(''), '');
      expect(tidyBackendMessage('   \n\n  '), '');
    });
  });

  group('splitTrailingUrl', () {
    test('lifts a trailing URL out of the prose', () {
      final (message, url) = splitTrailingUrl(
        'Try a large-cap US listing, or upgrade at https://example.com/pricing',
      );

      expect(url, 'https://example.com/pricing');
      expect(message, 'Try a large-cap US listing, or upgrade');
      expect(message, isNot(contains('http')));
    });

    test('returns the text unchanged when there is no URL', () {
      const text = 'Nothing to see here.';
      final (message, url) = splitTrailingUrl(text);
      expect(message, text);
      expect(url, isNull);
    });

    test('ignores a URL that is not at the end', () {
      const text = 'See https://example.com for details and then continue';
      final (message, url) = splitTrailingUrl(text);
      expect(url, isNull);
      expect(message, text);
    });

    test('the two together produce a clean message with a trailing link', () {
      const raw =
          'The ticker is valid, but this company is not covered.\n'
          '  Read more at https://example.com/coverage';

      final (message, url) = splitTrailingUrl(tidyBackendMessage(raw));

      expect(message, isNot(contains('\n')));
      expect(message, endsWith('Read more'));
      expect(url, 'https://example.com/coverage');
    });
  });
}
