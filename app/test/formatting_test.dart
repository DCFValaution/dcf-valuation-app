// Tests for reflowing backend messages written for a terminal.

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/formatting.dart';

void main() {
  group('tidyBackendMessage', () {
    // The exact string the backend returns for a plan-gated ticker, which
    // previously rendered with stray indentation mid-paragraph.
    const planLimited =
        "BRK-B isn't available on your current data plan.\n"
        '  The ticker is valid, but detailed financials for it need a paid '
        'FMP plan - the free tier covers only a subset of companies.\n'
        '  Try a large-cap US listing, or upgrade at '
        'https://site.financialmodelingprep.com/developer/docs/pricing';

    test('joins hard-wrapped lines and strips terminal indentation', () {
      final tidied = tidyBackendMessage(planLimited);

      expect(tidied.contains('\n'), isFalse);
      expect(tidied.contains('  '), isFalse, reason: 'no double spaces left');
      expect(tidied, startsWith("BRK-B isn't available"));
      expect(tidied, contains('plan. The ticker is valid'));
    });

    test('preserves genuine paragraph breaks', () {
      const input = 'First paragraph line one\nline two\n\nSecond paragraph';
      expect(tidyBackendMessage(input),
          'First paragraph line one line two\n\nSecond paragraph');
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
          'Try a large-cap US listing, or upgrade at https://example.com/pricing');

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

    test('the two together produce a clean plan-limited message', () {
      const raw = "BRK-B isn't available on your current data plan.\n"
          '  The ticker is valid, but detailed financials need a paid plan.\n'
          '  Upgrade at https://example.com/pricing';

      final (message, url) = splitTrailingUrl(tidyBackendMessage(raw));

      expect(message, isNot(contains('\n')));
      expect(message, endsWith('Upgrade'));
      expect(url, 'https://example.com/pricing');
    });
  });
}
