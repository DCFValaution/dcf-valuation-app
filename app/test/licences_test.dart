/// Attribution is an obligation, not a courtesy.
///
/// Almost everything this app is built on is BSD-3-Clause, and that licence
/// asks for the copyright notice to be reproduced in the materials shipped
/// with the binary. Flutter collects every bundled package's licence itself;
/// what it cannot do is put a door on it.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/theme.dart';

void main() {
  testWidgets('the disclaimer sheet offers the licences', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLightTheme(),
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showDisclaimerSheet(context),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('Open-source licences'), findsOneWidget);
  });

  testWidgets('and it opens a page that actually lists them', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLightTheme(),
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showDisclaimerSheet(context),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    // The sheet scrolls; on a short viewport the button sits below the fold.
    await tester.ensureVisible(find.text('Open-source licences'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open-source licences'));
    await tester.pumpAndSettle();

    expect(find.byType(LicensePage), findsOneWidget);
    expect(find.text('Intrinsic'), findsWidgets);
  });

  for (final entry in const [
    ('Privacy', 'no accounts, no sign-in'),
    ('Terms of use', 'provided as-is'),
  ]) {
    testWidgets('the ${entry.$1} sheet opens and says what it must', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: buildLightTheme(),
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => showDisclaimerSheet(context),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text(entry.$1));
      await tester.pumpAndSettle();
      await tester.tap(find.text(entry.$1));
      await tester.pumpAndSettle();

      expect(find.textContaining(entry.$2), findsOneWidget);
    });
  }

  testWidgets('privacy admits the third party most apps leave out', (
    tester,
  ) async {
    // The web build really does fetch its engine and font from Google, and
    // Google really does see an IP address when it does. A privacy page
    // that omits that is less trustworthy for the omission, not more.
    await tester.pumpWidget(
      MaterialApp(theme: buildLightTheme(), home: const SizedBox()),
    );
    final ctx = tester.element(find.byType(SizedBox));
    showPrivacySheet(ctx);
    await tester.pumpAndSettle();

    expect(find.textContaining('see your IP address'), findsOneWidget);
    expect(find.textContaining('never used to train'), findsOneWidget);
  });
}
