/// Optional accounts: the gate, the watchlist, saving, and the account
/// screen.
///
/// THE PROPERTY THE WHOLE FEATURE RESTS ON
/// ---------------------------------------
/// The sign-in sheet opens on a save action and nowhere else. Not on
/// launch, not on valuing a company, not on exporting the spreadsheet,
/// not on opening the Advanced panel. `TestTheGate` drives those paths and
/// asserts no sheet appears - because "we only ask when you save" is a
/// promise that decays the moment somebody adds a convenient prompt, and
/// a convention is not a guarantee.
///
/// The second property: declining costs nothing. The sheet closes, the
/// figure is still on screen, and nothing was stored.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:dcf_app/account_screen.dart';
import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/main.dart';
import 'package:dcf_app/save_actions.dart';
import 'package:dcf_app/saved_valuation.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/user_data_store.dart';
import 'package:dcf_app/valuation_api.dart';
import 'package:dcf_app/watchlist_screen.dart';

final _cases = jsonDecode(
  File('test/fixtures/relative_cases.json').readAsStringSync(),
) as Map<String, dynamic>;

ValuationApi fakeApi({
  List<Map<String, Object?>>? watchlistRows,
  List<String>? recordTo,
}) =>
    ValuationApi(
      baseUrl: 'http://test',
      client: MockClient((request) async {
        final path = request.url.path;
        recordTo?.add('${request.url}|${request.body}');
        if (path == '/watchlist') {
          return http.Response(
            jsonEncode({'rows': watchlistRows ?? const [], 'max_tickers': 50}),
            200,
            headers: const {'content-type': 'application/json'},
          );
        }
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

Future<void> valueTicker(WidgetTester tester, String ticker) async {
  await tester.enterText(find.byType(TextField).first, ticker);
  await tester.tap(find.widgetWithText(FilledButton, 'Value'));
  await tester.pump();
  await tester.pump();
}

final _sheet = find.byKey(const Key('sign-in-google'));

/// A store holding one save with a real assumption in it.
final savedStore = FakeUserDataStore()
  ..seedOldSave(
    'saved-1',
    const {'operating_margin': 0.42},
    kSchemaVersion,
    name: 'A saved model',
    ticker: 'AAPL',
  );

/// Open the account area and switch to the Saved tab.
Future<void> openSavedTab(WidgetTester tester) async {
  // The account screen is taller than the 800x600 test
  // window - which is shorter than any real phone - so the
  // row has to be scrolled to before it can be tapped.
  await tester.ensureVisible(find.byKey(const Key('account-button')));
  await tester.tap(find.byKey(const Key('account-button')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('tab-saved')));
  await tester.pumpAndSettle();
}

void main() {
  group('the gate', () {
    testWidgets('does not appear on launch', (tester) async {
      await tester.pumpWidget(DcfApp(
        api: fakeApi(),
        accounts: FakeAccountService(),
        path: '/',
      ));
      await tester.pump();

      expect(_sheet, findsNothing);
    });

    testWidgets('does not appear when a company is valued', (tester) async {
      // Valuing is the product. Asking who you are in order to do it would
      // be the whole bargain broken.
      await tester.pumpWidget(DcfApp(
        api: fakeApi(),
        accounts: FakeAccountService(),
        path: '/',
      ));
      await tester.pump();
      await valueTicker(tester, 'AAPL');
      await tester.pumpAndSettle();

      expect(_sheet, findsNothing);
      expect(find.textContaining(r'$'), findsWidgets,
          reason: 'a figure was produced, signed out');
    });

    testWidgets('appears when a valuation is saved', (tester) async {
      final accounts = FakeAccountService();
      await tester.pumpWidget(DcfApp(
        api: fakeApi(),
        accounts: accounts,
        path: '/',
      ));
      await tester.pump();
      await valueTicker(tester, 'AAPL');
      await tester.pumpAndSettle();

      final save = find.byKey(const Key('save-valuation'));
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();

      expect(_sheet, findsOneWidget);
      expect(find.text('Save this valuation'), findsOneWidget,
          reason: 'the sheet says what it interrupted');
    });

    testWidgets('appears when a company is added to the watchlist',
        (tester) async {
      await tester.pumpWidget(DcfApp(
        api: fakeApi(),
        accounts: FakeAccountService(),
        path: '/',
      ));
      await tester.pump();
      await valueTicker(tester, 'AAPL');
      await tester.pumpAndSettle();

      final watch = find.byKey(const Key('add-to-watchlist'));
      await tester.ensureVisible(watch);
      await tester.pumpAndSettle();
      await tester.tap(watch);
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('sign-in-reason')), findsOneWidget);
      expect(find.textContaining('watchlist'), findsWidgets);
    });

    testWidgets('declining costs nothing', (tester) async {
      final accounts = FakeAccountService();
      await tester.pumpWidget(DcfApp(
        api: fakeApi(),
        accounts: accounts,
        path: '/',
      ));
      await tester.pump();
      await valueTicker(tester, 'AAPL');
      await tester.pumpAndSettle();

      final save = find.byKey(const Key('save-valuation'));
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('sign-in-not-now')));
      await tester.pumpAndSettle();

      expect(_sheet, findsNothing);
      expect(accounts.current, isNull);
      expect(accounts.calls, isNot(contains('signIn')));
      expect(find.byKey(const Key('save-valuation')), findsOneWidget,
          reason: 'the figure and its buttons are exactly as they were');
    });

    testWidgets('the account button is absent when Firebase did not start',
        (tester) async {
      // A button that opens a page about a feature this build cannot
      // offer is worse than no button.
      await tester.pumpWidget(DcfApp(api: fakeApi(), accounts: null, path: '/'));
      await tester.pump();

      expect(find.byKey(const Key('account-button')), findsNothing);
    });
  });

  group('saving', () {
    testWidgets('stores the assumptions under the signed-in account',
        (tester) async {
      final store = FakeUserDataStore();
      final accounts = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'));

      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: Scaffold(
          body: SaveActions(
            ticker: 'NVDA',
            companyName: 'NVIDIA Corporation',
            assumptions: const {'terminal_roic': 0.25},
            value: 219.33,
            accounts: accounts,
            storeFor: (_) => store,
          ),
        ),
      ));

      await tester.tap(find.byKey(const Key('save-valuation')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('save-confirm')));
      await tester.pumpAndSettle();

      expect(store.calls, contains('putSave:NVDA'));
      expect(_sheet, findsNothing, reason: 'already signed in');
    });

    testWidgets('suggests a name that says how much was changed',
        (tester) async {
      // "NVDA" alone is useless the moment there are two of them.
      final accounts = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'));

      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: Scaffold(
          body: SaveActions(
            ticker: 'NVDA',
            companyName: 'NVIDIA Corporation',
            assumptions: const {'terminal_roic': 0.25, 'wacc': 0.10},
            value: 219.33,
            accounts: accounts,
            storeFor: (_) => FakeUserDataStore(),
          ),
        ),
      ));

      await tester.tap(find.byKey(const Key('save-valuation')));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(
          find.byKey(const Key('save-name-field')));
      expect(field.controller?.text, 'NVDA — 2 assumptions changed');
    });

    testWidgets('says what a save actually keeps', (tester) async {
      final accounts = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'));

      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: Scaffold(
          body: SaveActions(
            ticker: 'NVDA',
            companyName: 'NVIDIA Corporation',
            assumptions: const {},
            value: 219.33,
            accounts: accounts,
            storeFor: (_) => FakeUserDataStore(),
          ),
        ),
      ));

      await tester.tap(find.byKey(const Key('save-valuation')));
      await tester.pumpAndSettle();

      expect(find.textContaining('assumptions are saved, not the figures'),
          findsOneWidget);
      expect(find.textContaining('recomputes'), findsOneWidget);
    });
  });

  group('reopening a save', () {
    testWidgets('its assumptions survive the valuation that follows',
        (tester) async {
      // The bug this pins: the assumptions went onto the sliders and then
      // `_submit()` reset the sliders to the derived model - as it must,
      // since that is also how a fresh company is valued. The save
      // reopened at the derived figure, which is the one number a save is
      // NOT for.
      //
      // Found by opening a real v1 document on the emulator, not a
      // hand-made fixture.
      final sent = <String>[];
      await tester.pumpWidget(DcfApp(
        api: fakeApi(recordTo: sent),
        accounts: FakeAccountService(
            signedIn: const Account(uid: 'uid-1', email: 'a@example.com')),
        storeFor: (_) => savedStore,
        path: '/',
      ));
      await tester.pump();

      await openSavedTab(tester);
      await tester.tap(find.text('A saved model'));
      await tester.pumpAndSettle();

      expect(
        sent.join(' | '),
        contains('operating_margin'),
        reason: 'the saved assumption has to reach the backend, or the '
            'figure on screen is the derived one wearing the name of a '
            'save',
      );
    });
  });

  group('the watchlist screen', () {
    Widget wrap(UserDataStore store, {List<Map<String, Object?>>? rows}) =>
        MaterialApp(
          theme: buildDarkTheme(),
          home: Scaffold(
            body: WatchlistScreen(store: store, api: fakeApi(watchlistRows: rows)),
          ),
        );

    testWidgets('an empty watchlist says how to fill it', (tester) async {
      await tester.pumpWidget(wrap(FakeUserDataStore()));
      await tester.pumpAndSettle();

      expect(find.textContaining('Nothing on your watchlist yet'),
          findsOneWidget);
      expect(find.textContaining('add it from the valuation screen'),
          findsOneWidget);
    });

    testWidgets('rows paint from the store before the API answers',
        (tester) async {
      // The whole reason the screen reads Firestore first: waking the free
      // Render instance took 19.4 seconds when measured, and a watchlist
      // that waited on it would be a spinner rather than a screen.
      final store = FakeUserDataStore();
      await store.addTicker('NVDA');
      await store.cacheRows([
        WatchlistEntry(
          ticker: 'NVDA',
          name: 'NVIDIA Corporation',
          lastValue: 219.33,
          lastPrice: 125.53,
          asOf: DateTime.now(),
        ),
      ]);

      await tester.pumpWidget(wrap(store));
      await tester.pump();

      expect(find.text('NVDA'), findsOneWidget);
      expect(find.text(r'$219.33'), findsOneWidget);
      expect(find.text('+74.7%'), findsOneWidget);
    });

    testWidgets('a company only just added reads as not valued yet',
        (tester) async {
      final store = FakeUserDataStore();
      await store.addTicker('KO');

      await tester.pumpWidget(wrap(store));
      await tester.pump();

      expect(find.text('KO'), findsOneWidget);
      expect(find.text('—'), findsWidgets);
    });

    testWidgets('a refused company keeps its row and says why',
        (tester) async {
      // Hiding Tesla would make the watchlist disagree with the valuation
      // screen, which says out loud that no figure is shown and why.
      final store = FakeUserDataStore();
      await store.addTicker('TSLA');
      await store.cacheRows([
        WatchlistEntry(
          ticker: 'TSLA',
          name: 'Tesla, Inc.',
          lastPrice: 431.20,
          refused: true,
          asOf: DateTime.now(),
        ),
      ]);

      await tester.pumpWidget(wrap(store));
      await tester.pump();

      expect(find.text('No figure'), findsOneWidget);
      expect(find.text('Refused'), findsOneWidget);
      expect(find.textContaining('isn’t the right tool here'), findsOneWidget);
    });

    testWidgets('a failed refresh keeps what was on screen', (tester) async {
      // The rows are real figures with real dates. Blanking them would be
      // worse than the staleness, and the status line is where the failure
      // belongs.
      final store = FakeUserDataStore();
      await store.addTicker('NVDA');
      await store.cacheRows([
        WatchlistEntry(
          ticker: 'NVDA',
          lastValue: 219.33,
          lastPrice: 125.53,
          asOf: DateTime.now(),
        ),
      ]);

      final broken = ValuationApi(
        baseUrl: 'http://test',
        client: MockClient((_) async => http.Response('nope', 500)),
      );
      await tester.pumpWidget(MaterialApp(
        theme: buildDarkTheme(),
        home: Scaffold(body: WatchlistScreen(store: store, api: broken)),
      ));
      await tester.pumpAndSettle();

      expect(find.text(r'$219.33'), findsOneWidget);
      expect(find.textContaining('Showing what you last saw'), findsOneWidget);
    });
  });

  group('the account screen', () {
    Widget wrap(AccountService service, {int watch = 0, int saves = 0}) =>
        MaterialApp(
          theme: buildDarkTheme(),
          home: AccountScreen(
            service: service,
            watchlistCount: watch,
            savesCount: saves,
          ),
        );

    testWidgets('leads with what is held', (tester) async {
      // That list is the whole of what signing in costs a person, and a
      // privacy policy nobody opens is not where it should first appear.
      await tester.pumpWidget(wrap(
        FakeAccountService(
            signedIn: const Account(uid: 'uid-1', email: 'a@example.com')),
        watch: 6,
        saves: 3,
      ));
      await tester.pumpAndSettle();

      // Set as an eyebrow label, like every other section heading in
      // the app, rather than as sentence case on its own.
      expect(find.text('WHAT WE HOLD'), findsOneWidget);
      expect(find.text('6 companies'), findsOneWidget);
      expect(find.text('3 saves'), findsOneWidget);
    });

    testWidgets('and counts one of something as one', (tester) async {
      // "1 saves" was on screen during the emulator run. A count beside a
      // noun is the easiest copy in the app to get wrong and the easiest
      // to notice.
      await tester.pumpWidget(wrap(
        FakeAccountService(
            signedIn: const Account(uid: 'uid-1', email: 'a@example.com')),
        watch: 1,
        saves: 1,
      ));
      await tester.pumpAndSettle();

      expect(find.text('1 company'), findsOneWidget);
      expect(find.text('1 save'), findsOneWidget);
      expect(find.byKey(const Key('account-nothing-else')), findsOneWidget);
    });

    testWidgets('counts the consequence rather than describing it',
        (tester) async {
      await tester.pumpWidget(wrap(
        FakeAccountService(
            signedIn: const Account(uid: 'uid-1', email: 'a@example.com')),
        watch: 6,
        saves: 3,
      ));
      await tester.pumpAndSettle();

      expect(
        find.textContaining('your 6 watchlist companies and your 3 saved '
            'valuations'),
        findsOneWidget,
      );
      expect(find.textContaining('cannot be undone'), findsOneWidget);
      expect(find.textContaining('keeps working without an account'),
          findsOneWidget);
    });

    testWidgets('offers the web route as well as the button', (tester) async {
      // Play requires both, and somebody who has uninstalled can only use
      // the second.
      await tester.pumpWidget(wrap(FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))));
      await tester.pumpAndSettle();

      expect(find.textContaining('intrinsic-web.onrender.com/delete'),
          findsOneWidget);
    });

    testWidgets('deletion has to be typed, not tapped', (tester) async {
      // A dialog with two buttons is dismissed by muscle memory.
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'));
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();

      final deleteButton = find.byKey(const Key('account-delete'));
      await tester.ensureVisible(deleteButton);
      await tester.pumpAndSettle();
      await tester.tap(deleteButton);
      await tester.pumpAndSettle();

      final confirm = find.byKey(const Key('account-delete-final'));
      expect(tester.widget<FilledButton>(confirm).onPressed, isNull,
          reason: 'inert until the word is typed');

      await tester.enterText(
          find.byKey(const Key('account-delete-confirm-field')), 'DELETE');
      await tester.pumpAndSettle();

      expect(tester.widget<FilledButton>(confirm).onPressed, isNotNull);
      await tester.tap(confirm);
      await tester.pumpAndSettle();

      expect(service.calls, contains('deleteAccount'));
    });

    testWidgets('a failed deletion says nothing was removed', (tester) async {
      final service = FakeAccountService(
          signedIn: const Account(uid: 'uid-1', email: 'a@example.com'))
        ..failNextDelete = 'the network dropped';
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();

      final deleteButton = find.byKey(const Key('account-delete'));
      await tester.ensureVisible(deleteButton);
      await tester.pumpAndSettle();
      await tester.tap(deleteButton);
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('account-delete-confirm-field')), 'DELETE');
      await tester.pumpAndSettle();
      // The account screen is taller than the 800x600 test
      // window - which is shorter than any real phone - so the
      // row has to be scrolled to before it can be tapped.
      await tester.ensureVisible(find.byKey(const Key('account-delete-final')));
      await tester.tap(find.byKey(const Key('account-delete-final')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Nothing has been removed'), findsOneWidget);
    });

    testWidgets('signed out, it says the app still works', (tester) async {
      await tester.pumpWidget(wrap(FakeAccountService()));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('account-signed-out')), findsOneWidget);
      expect(find.byKey(const Key('account-delete')), findsNothing);
    });
  });
}
