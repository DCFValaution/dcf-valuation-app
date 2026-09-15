// After a valuation, closing any route must not hand focus back to the
// ticker field.
//
// Found on the emulator through the add-peer sheet, but older than it: the
// disclaimer sheet did the same. Pressing Value unfocused the page's scope,
// which hides the keyboard but leaves the field as the scope's remembered
// child - so the next route to close restored focus to it and the keyboard
// sprang up over the result. The field itself must be unfocused instead.

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/main.dart';
import 'package:dcf_app/valuation_api.dart';

void main() {
  testWidgets(
    'closing the disclaimer after a valuation leaves the keyboard down',
    (tester) async {
      final aapl =
          (jsonDecode(
            File('test/fixtures/method_cases.json').readAsStringSync(),
          ) as List).cast<Map<String, dynamic>>().firstWhere(
            (c) => c['ticker'] == 'AAPL',
          )['body'];
      final api = ValuationApi(
        baseUrl: 'http://test',
        client: MockClient(
          (r) async => r.url.path == '/valuation/AAPL'
              ? http.Response(jsonEncode(aapl), 200)
              : http.Response('{"status":"ok","query":"","results":[]}', 200),
        ),
      );
      await tester.pumpWidget(DcfApp(api: api));

      // Type a ticker and press Value - the path that left the field remembered.
      await tester.enterText(find.byType(TextField), 'AAPL');
      await tester.tap(find.widgetWithText(FilledButton, 'Value'));
      await tester.pump();
      await tester.pump();

      await tester.tap(find.byTooltip('About and disclaimer'));
      await tester.pumpAndSettle();
      Navigator.of(tester.element(find.byType(BottomSheet))).pop();
      await tester.pumpAndSettle();

      final field = tester.state<EditableTextState>(
        find.descendant(
          of: find.byType(TextField).first,
          matching: find.byType(EditableText),
        ),
      );
      expect(field.widget.focusNode.hasFocus, isFalse);
      expect(tester.testTextInput.isVisible, isFalse, reason: 'no keyboard');
    },
  );
}
