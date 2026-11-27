/// The saved-valuations list: open, rename, delete — and the badge.
///
/// A save holds assumptions, not figures, so the list shows what the
/// figure was WHEN IT WAS SAVED and opening one recomputes from today's
/// market data. That comparison is the whole feature.
///
/// The property these tests exist for is the one in `the badge` and
/// `opening an old save`: a save written under an older model is marked
/// before it is opened and explains itself when it is. The model has
/// already changed shape twice in ways that invalidate a stored
/// assumption set, so this is not a hypothetical - a save made the week
/// before 1.0.41 refers to three fields that no longer exist.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/saved_valuation.dart';
import 'package:dcf_app/saves_screen.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/user_data_store.dart';

Widget wrap(UserDataStore store,
        {void Function(SavedRef, LoadedSave)? onOpen}) =>
    MaterialApp(
      theme: buildDarkTheme(),
      home: Scaffold(body: SavesScreen(store: store, onOpen: onOpen)),
    );

/// A save as this build writes them.
Future<FakeUserDataStore> withCurrentSave() async {
  final store = FakeUserDataStore();
  await store.putSave(
    name: 'NVDA — Damodaran growth path',
    ticker: 'NVDA',
    assumptions: const {'years_two_to_five_growth': 0.20},
    savedValue: 219.33,
  );
  return store;
}

/// A save written before the growth structure existed.
FakeUserDataStore withV1Save() {
  final store = FakeUserDataStore();
  store.seedOldSave(
    'old-1',
    const {
      'revenue_growth': 0.15,
      'operating_margin': 0.30,
      'projection_years': 5,
      'fade_enabled': 1,
      'fade_start_year': 3,
    },
    1,
    name: 'NVDA — my 2025 model',
  );
  return store;
}

void main() {
  group('an empty list', () {
    testWidgets('says how to fill it and what a save actually is',
        (tester) async {
      await tester.pumpWidget(wrap(FakeUserDataStore()));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('saves-empty')), findsOneWidget);
      expect(find.textContaining('assumptions are kept'), findsOneWidget);
      expect(find.textContaining('recomputed each time'), findsOneWidget);
    });
  });

  group('the list', () {
    testWidgets('shows the name, the ticker and the figure at save time',
        (tester) async {
      await tester.pumpWidget(wrap(await withCurrentSave()));
      await tester.pumpAndSettle();

      expect(find.text('NVDA — Damodaran growth path'), findsOneWidget);
      expect(find.text('NVDA'), findsOneWidget);
      expect(find.text(r'$219.33'), findsOneWidget);
      expect(find.text('the figure when you saved it'), findsOneWidget,
          reason: 'otherwise it reads as today’s figure, which it is not');
    });

    testWidgets('a current save carries no badge', (tester) async {
      // A mark that appears on everything tells a reader nothing.
      final store = await withCurrentSave();
      await tester.pumpWidget(wrap(store));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('save-badge-s1')), findsNothing);
    });
  });

  group('the badge', () {
    testWidgets('marks an older save before it is opened', (tester) async {
      await tester.pumpWidget(wrap(withV1Save()));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('save-badge-old-1')), findsOneWidget);
      expect(find.textContaining('Saved under an older model (v1)'),
          findsOneWidget);
    });
  });

  group('opening an old save', () {
    testWidgets('says what no longer applies, in the reader’s own figures',
        (tester) async {
      await tester.pumpWidget(wrap(withV1Save()));
      await tester.pumpAndSettle();

      await tester.tap(find.text('NVDA — my 2025 model'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('what-changed')), findsOneWidget);
      expect(find.textContaining('You saved this as v1'), findsOneWidget);
      // Listed, not summarised: "some assumptions have changed" tells a
      // reader nothing they can check, and checking is the point.
      expect(find.text('Revenue growth'), findsOneWidget);
      expect(find.text('Forecast length'), findsOneWidget);
      expect(find.text('Growth fade'), findsOneWidget);
      expect(find.textContaining('15.0%'), findsOneWidget);
      expect(find.textContaining('5-year forecast'), findsOneWidget);
    });

    testWidgets('promises the save itself is untouched', (tester) async {
      // Accepting the new shape is the reader's act, which happens when
      // they save again - not when they look.
      await tester.pumpWidget(wrap(withV1Save()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('NVDA — my 2025 model'));
      await tester.pumpAndSettle();

      expect(find.textContaining('Nothing has been changed on the save'),
          findsOneWidget);
    });

    testWidgets('hands back the migrated assumptions, not the stored ones',
        (tester) async {
      SavedRef? openedRef;
      LoadedSave? opened;
      await tester.pumpWidget(wrap(withV1Save(), onOpen: (r, l) {
        openedRef = r;
        opened = l;
      }));
      await tester.pumpAndSettle();

      await tester.tap(find.text('NVDA — my 2025 model'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('what-changed-ok')));
      await tester.pumpAndSettle();

      expect(openedRef?.ticker, 'NVDA');
      expect(opened?.assumptions['years_two_to_five_growth'], 0.15,
          reason: 'the single v1 rate becomes the years 2–5 rate');
      expect(opened?.assumptions.containsKey('projection_years'), isFalse,
          reason: 'sending a retired override would refuse the valuation');
      expect(opened?.assumptions.containsKey('fade_enabled'), isFalse);
    });

    testWidgets('a current save opens with no interruption', (tester) async {
      LoadedSave? opened;
      final store = await withCurrentSave();
      await tester.pumpWidget(wrap(store, onOpen: (_, l) => opened = l));
      await tester.pumpAndSettle();

      await tester.tap(find.text('NVDA — Damodaran growth path'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('what-changed')), findsNothing);
      expect(opened?.assumptions['years_two_to_five_growth'], 0.20);
    });

    testWidgets('a save from a newer build opens read-only', (tester) async {
      // A web client can be ahead of an Android one. Half-understanding a
      // shape this build does not know is the silent-reinterpretation
      // failure wearing a different hat.
      bool handedBack = false;
      final store = FakeUserDataStore()
        ..seedOldSave('future-1', const {'something_new': 0.5},
            kSchemaVersion + 1, name: 'From the future');
      await tester.pumpWidget(wrap(store, onOpen: (_, _) => handedBack = true));
      await tester.pumpAndSettle();

      expect(find.textContaining('Saved in a newer version'), findsOneWidget);

      await tester.tap(find.text('From the future'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('what-changed-ok')));
      await tester.pumpAndSettle();

      expect(handedBack, isFalse, reason: 'nothing is applied from it');
    });
  });

  group('rename', () {
    testWidgets('changes the name', (tester) async {
      final store = await withCurrentSave();
      await tester.pumpWidget(wrap(store));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('save-menu-s1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Rename'));
      await tester.pumpAndSettle();

      await tester.enterText(
          find.byKey(const Key('rename-field')), 'NVDA — the cautious one');
      await tester.tap(find.byKey(const Key('rename-confirm')));
      await tester.pumpAndSettle();

      expect(store.calls, contains('renameSave:s1'));
      expect(find.text('NVDA — the cautious one'), findsOneWidget);
    });

    testWidgets('does not stamp the current version onto an old save',
        (tester) async {
      // Renaming is not accepting a new shape. If it were, a reader could
      // lose the explanation they have never seen by tidying up a list.
      final store = withV1Save();
      await tester.pumpWidget(wrap(store));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('save-menu-old-1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Rename'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('rename-field')), 'Renamed');
      await tester.tap(find.byKey(const Key('rename-confirm')));
      await tester.pumpAndSettle();

      expect(find.text('Renamed'), findsOneWidget);
      expect(find.textContaining('Saved under an older model (v1)'),
          findsOneWidget,
          reason: 'still v1, because renaming accepted nothing');
    });
  });

  group('delete', () {
    testWidgets('asks first, and says what goes', (tester) async {
      final store = await withCurrentSave();
      await tester.pumpWidget(wrap(store));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('save-menu-s1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();

      expect(find.textContaining('The company itself is unaffected'),
          findsOneWidget);

      await tester.tap(find.byKey(const Key('save-delete-confirm')));
      await tester.pumpAndSettle();

      expect(store.calls, contains('deleteSave:s1'));
      expect(find.byKey(const Key('saves-empty')), findsOneWidget);
    });

    testWidgets('cancelling keeps it', (tester) async {
      final store = await withCurrentSave();
      await tester.pumpWidget(wrap(store));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('save-menu-s1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      expect(store.calls, isNot(contains('deleteSave:s1')));
      expect(find.text('NVDA — Damodaran growth path'), findsOneWidget);
    });
  });
}
