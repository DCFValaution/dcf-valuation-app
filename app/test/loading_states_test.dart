/// No screen may spin forever.
///
/// THE BUG THIS EXISTS FOR
/// -----------------------
/// Every screen behind an account was written as:
///
///     if (snapshot.data == null) return CircularProgressIndicator();
///
/// which shows a spinner when the stream is still loading AND when it has
/// failed. The Firestore rules were never deployed to the real project,
/// so every read came back `PERMISSION_DENIED`, and every one of those
/// screens span forever with the reason sitting in `snapshot.error`,
/// unread.
///
/// A spinner that cannot stop is the worst state a screen has: it looks
/// exactly like slowness, so the reader cannot tell whether to wait or to
/// act, and there is nothing to report but "it hangs".
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/loading_states.dart';
import 'package:dcf_app/saved_valuation.dart';
import 'package:dcf_app/saves_screen.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/user_data_store.dart';
import 'package:dcf_app/valuation_api.dart';
import 'package:dcf_app/watchlist_screen.dart';

/// What Firestore actually throws when rules deny a read.
class FakeFirestoreDenied implements Exception {
  @override
  String toString() =>
      '[cloud_firestore/permission-denied] Missing or insufficient '
      'permissions.';
}

/// A store whose every read fails, the way the undeployed rules did.
class DeniedStore implements UserDataStore {
  int watchlistSubscriptions = 0;

  @override
  Stream<List<WatchlistEntry>> watchlist() {
    watchlistSubscriptions++;
    return Stream<List<WatchlistEntry>>.error(FakeFirestoreDenied());
  }

  @override
  Stream<List<SavedRef>> saves() =>
      Stream<List<SavedRef>>.error(FakeFirestoreDenied());

  @override
  Future<void> addTicker(String ticker) async {}
  @override
  Future<void> removeTicker(String ticker) async {}
  @override
  Future<void> cacheRows(List<WatchlistEntry> rows) async {}
  @override
  Future<String> putSave({
    String? id,
    required String name,
    required String ticker,
    required Map<String, double> assumptions,
    double? savedValue,
  }) async =>
      'x';
  @override
  Future<LoadedSave> openSave(String id) async => throw UnimplementedError();
  @override
  Future<void> renameSave(String id, String name) async {}
  @override
  Future<void> deleteSave(String id) async {}
}

Widget wrap(Widget child) =>
    MaterialApp(theme: buildDarkTheme(), home: Scaffold(body: child));

void main() {
  group('a stream that fails', () {
    testWidgets('says so instead of spinning', (tester) async {
      await tester.pumpWidget(wrap(LoadingStream<int>(
        stream: () => Stream<int>.error(FakeFirestoreDenied()),
        what: 'the thing',
        builder: (_, v) => Text('$v'),
      )));
      await tester.pumpAndSettle();

      expect(find.byType(CircularProgressIndicator), findsNothing,
          reason: 'this is the whole bug');
      expect(find.byKey(const Key('failed-to-load')), findsOneWidget);
      expect(find.text('Could not load the thing'), findsOneWidget);
    });

    testWidgets('names what failed, not just that something did',
        (tester) async {
      // "Something went wrong" leaves the reader guessing which part of
      // the screen is broken.
      await tester.pumpWidget(wrap(LoadingStream<int>(
        stream: () => Stream<int>.error(FakeFirestoreDenied()),
        what: 'your watchlist',
        builder: (_, v) => Text('$v'),
      )));
      await tester.pumpAndSettle();

      expect(find.text('Could not load your watchlist'), findsOneWidget);
    });

    testWidgets('a denial says the saved work is safe', (tester) async {
      // The reader's first fear on seeing an error about their own data
      // is that it is gone. It is not, and saying so costs one sentence.
      await tester.pumpWidget(wrap(LoadingStream<int>(
        stream: () => Stream<int>.error(FakeFirestoreDenied()),
        what: 'your saved valuations',
        builder: (_, v) => Text('$v'),
      )));
      await tester.pumpAndSettle();

      expect(find.textContaining('refused the request'), findsOneWidget);
      expect(find.textContaining('Nothing is lost'), findsOneWidget);
    });

    testWidgets('offers a retry that really re-subscribes', (tester) async {
      // A retry that rebuilds the same dead stream is a button that lies.
      var attempts = 0;
      await tester.pumpWidget(wrap(LoadingStream<int>(
        stream: () {
          attempts++;
          return attempts == 1
              ? Stream<int>.error(FakeFirestoreDenied())
              : Stream<int>.value(42);
        },
        what: 'the thing',
        builder: (_, v) => Text('loaded $v'),
      )));
      await tester.pumpAndSettle();
      expect(attempts, 1);

      await tester.tap(find.byKey(const Key('failed-to-load-retry')));
      await tester.pumpAndSettle();

      expect(attempts, 2, reason: 'a new subscription, not a rebuild');
      expect(find.text('loaded 42'), findsOneWidget);
    });

    testWidgets('still spins while it is genuinely loading', (tester) async {
      // The fix must not make a slow load look like a failure.
      final controller = StreamController<int>();
      await tester.pumpWidget(wrap(LoadingStream<int>(
        stream: () => controller.stream,
        what: 'the thing',
        builder: (_, v) => Text('loaded $v'),
      )));
      await tester.pump();

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(find.byKey(const Key('failed-to-load')), findsNothing);

      controller.add(7);
      await tester.pumpAndSettle();
      expect(find.text('loaded 7'), findsOneWidget);
      await controller.close();
    });
  });

  group('the real screens', () {
    testWidgets('the watchlist shows the failure and retries', (tester) async {
      final store = DeniedStore();
      await tester.pumpWidget(wrap(WatchlistScreen(
        store: store,
        api: ValuationApi(baseUrl: 'http://test'),
      )));
      await tester.pumpAndSettle();

      expect(find.byType(CircularProgressIndicator), findsNothing);
      expect(find.text('Could not load your watchlist'), findsOneWidget);
      expect(store.watchlistSubscriptions, 1);

      await tester.tap(find.byKey(const Key('failed-to-load-retry')));
      await tester.pumpAndSettle();
      expect(store.watchlistSubscriptions, 2);
    });

    testWidgets('saved valuations show the failure', (tester) async {
      await tester.pumpWidget(wrap(SavesScreen(store: DeniedStore())));
      await tester.pumpAndSettle();

      expect(find.byType(CircularProgressIndicator), findsNothing);
      expect(find.text('Could not load your saved valuations'),
          findsOneWidget);
    });

    testWidgets('an empty watchlist is still empty, not failed',
        (tester) async {
      // The two look alike from a distance and mean opposite things.
      await tester.pumpWidget(wrap(WatchlistScreen(
        store: FakeUserDataStore(),
        api: ValuationApi(baseUrl: 'http://test'),
      )));
      await tester.pumpAndSettle();

      expect(find.textContaining('Nothing on your watchlist yet'),
          findsOneWidget);
      expect(find.byKey(const Key('failed-to-load')), findsNothing);
    });
  });
}
