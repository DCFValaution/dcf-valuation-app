/// A harness for photographing the real screens.
///
/// NOT A MOCKUP. Every widget below is the one that ships -
/// `AccountScreen`, `ValuationGateView`, `FreeCountPill`,
/// `LastFreeValuationCard`, `SignInWithGoogleButton` - imported from
/// lib/ and rendered unmodified. Nothing here reimplements a screen.
///
/// What IS supplied is the state they would have been given at runtime:
/// a `FakeAccountService` (the same one the test suite uses) and a fixed
/// `Allowance` in place of the one the server sends in response headers.
/// That is the only way to photograph the gate's offline state, or the
/// last-free card, without arranging a real network failure and really
/// spending a real allowance.
///
///     flutter run -t screens/main.dart -d <device>
///
/// Delete this directory once the screenshots are taken.
///
/// The bar across the bottom is HARNESS CHROME - it switches screen and
/// theme so one build covers every state - and is not part of any screen.
library;

import 'package:flutter/material.dart';

import 'package:dcf_app/account_screen.dart';
import 'package:dcf_app/account_service.dart';
import 'package:dcf_app/allowance.dart';
import 'package:dcf_app/free_valuations.dart';
import 'package:dcf_app/google_sign_in_button.dart';
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/valuation_api.dart';
import 'package:dcf_app/valuation_gate.dart';

void main() => runApp(const ShotsApp());

/// Stands in for `dart:io`'s, which a web build cannot name. The gate
/// classifies by runtime type NAME precisely so that one screen can treat
/// the same failure the same way on both platforms.
class SocketException implements Exception {
  const SocketException(this.message);
  final String message;

  @override
  String toString() => 'SocketException: $message';
}

/// Signed in, with something to show in "What we hold".
AccountService signedInService() => FakeAccountService(
      signedIn: const Account(uid: 'uid-1', email: 'a.reader@gmail.com'),
    )..countedValuations = 18;

/// The anonymous account every installation has from first launch.
AccountService anonymousService() => FakeAccountService(
      signedIn: const Account(uid: 'anon-install-1', isAnonymous: true),
    );

const gatedLifetime = ValuationGated(
  message: 'You have used your 3 free valuations. Signing in with Google '
      'lifts the limit — it is free, and it keeps your work too.',
  limit: 3,
  window: 'lifetime',
  signInLiftsLimit: true,
  isNetworkLimit: false,
);

class ShotsApp extends StatefulWidget {
  const ShotsApp({super.key});

  @override
  State<ShotsApp> createState() => _ShotsAppState();
}

class _ShotsAppState extends State<ShotsApp> {
  /// Taken from the URL - `?s=3&dark=1` - so a screenshot is ONE
  /// navigation with no clicks.
  ///
  /// Not a nicety: CanvasKit does not reliably re-lay-out after a
  /// viewport change followed by taps, so a two-click route to a state
  /// produced a canvas wider than the window and screenshots with the
  /// right-hand side cut off. Loading straight into the state avoids the
  /// interaction entirely.
  late ThemeMode _mode =
      Uri.base.queryParameters['dark'] == '1' ? ThemeMode.dark : ThemeMode.light;
  late int _screen =
      int.tryParse(Uri.base.queryParameters['s'] ?? '') ?? 0;

  static const _names = [
    'Account', 'Anon', 'Gate', 'Net', 'Fail', 'Offline', 'Counter',
    'Button',
  ];

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Screens',
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      themeMode: _mode,
      home: Builder(
        builder: (context) => Scaffold(
          body: switch (_screen) {
            // The shipping screen, whole.
            0 => AccountScreen(
                service: signedInService(),
                watchlistCount: 6,
                savesCount: 3,
              ),
            1 => AccountScreen(service: anonymousService()),
            2 => _GatePage(service: anonymousService()),
            // The gate's two failure states. Reached by tapping the
            // sign-in button, which is the only way the real screen
            // enters them - the state belongs to the widget, and nothing
            // here sets it from outside.
            // The per-network cap. A reader can meet this having valued
            // nothing at all, so it must never claim they spent their own
            // three.
            3 => _GatePage(
                service: anonymousService(),
                gated: const ValuationGated(
                  message: 'Free valuations from this network are used up '
                      'for today. Signing in with Google is free and lifts '
                      'the limit.',
                  limit: 200,
                  window: 'day',
                  signInLiftsLimit: true,
                  isNetworkLimit: true,
                ),
              ),
            4 => _GatePage(
                service: FakeAccountService(
                  signedIn: const Account(
                      uid: 'anon-install-1', isAnonymous: true),
                )..failNextSignIn = 'Google turned the request down.',
              ),
            5 => _GatePage(
                service: FakeAccountService(
                  signedIn: const Account(
                      uid: 'anon-install-1', isAnonymous: true),
                )..throwNextSignIn = const SocketException('connection reset'),
              ),
            6 => const _CounterPage(),
            _ => const _ButtonPage(),
          },
          bottomNavigationBar: _chrome(context),
        ),
      ),
    );
  }

  Widget _chrome(BuildContext context) => Container(
        color: context.scheme.surfaceContainerHighest,
        padding: const EdgeInsets.fromLTRB(4, 2, 4, 2),
        child: SafeArea(
          top: false,
          child: Row(
            children: [
              for (var i = 0; i < _names.length; i++)
                Expanded(
                  child: TextButton(
                    onPressed: () => setState(() => _screen = i),
                    style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      foregroundColor: i == _screen
                          ? context.scheme.primary
                          : context.colors.textSecondary,
                    ),
                    child: Text(_names[i],
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: i == _screen
                              ? FontWeight.w700
                              : FontWeight.w500,
                        )),
                  ),
                ),
              IconButton(
                iconSize: 17,
                visualDensity: VisualDensity.compact,
                onPressed: () => setState(() => _mode = _mode == ThemeMode.light
                    ? ThemeMode.dark
                    : ThemeMode.light),
                icon: Icon(
                  _mode == ThemeMode.light
                      ? Icons.dark_mode_outlined
                      : Icons.light_mode_outlined,
                  color: context.colors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      );
}

/// The gate, in the frame the app gives it: an app bar and a scroll view.
class _GatePage extends StatelessWidget {
  const _GatePage({required this.service, this.gated = gatedLifetime});

  final AccountService service;
  final ValuationGated gated;

  @override
  Widget build(BuildContext context) => Column(
        children: [
          AppBar(
            title: const Text('Sign in to continue'),
            leading: const Icon(Icons.arrow_back_rounded),
          ),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(
                  AppSpacing.lg, AppSpacing.lg, AppSpacing.lg, AppSpacing.xxxl),
              child: ValuationGateView(
                gated: gated,
                service: service,
                // Null, always. The gate is shown to an anonymous
                // reader, who cannot keep a save - so the real app passes
                // null here too, and the screen says the true useful
                // thing instead of offering a button to an empty page.
                onOpenSaved: null,
                onBack: () {},
              ),
            ),
          ),
        ],
      );
}

/// The pill in the header, and the card on the last one.
class _CounterPage extends StatelessWidget {
  const _CounterPage();

  /// ONE allowance, feeding both. The pill prints `remaining` and the
  /// card appears only when `onLastOne`, exactly as main.dart wires them -
  /// so a harness that showed "2 free valuations left" above "One free
  /// valuation left" was showing a state the app cannot produce.
  static const allowance = Allowance(
    plan: 'anonymous',
    window: 'lifetime',
    limit: 3,
    remaining: 1,
  );

  @override
  Widget build(BuildContext context) => Column(
        children: [
          AppBar(
            title: const Text('Intrinsic'),
            actions: const [
              Padding(
                padding: EdgeInsets.only(right: AppSpacing.xs),
                child: Center(child: FreeCountPill(allowance: allowance)),
              ),
              Icon(Icons.account_circle_outlined),
              SizedBox(width: AppSpacing.lg),
            ],
          ),
          Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const TextField(
                  decoration: InputDecoration(
                    hintText: 'Ticker or company name',
                    prefixIcon: Icon(Icons.search_rounded, size: 20),
                  ),
                ),
                const SizedBox(height: AppGap.block),
                SizedBox(
                  height: 52,
                  child: FilledButton(
                      onPressed: () {},
                      child: const Text('Value this company')),
                ),
                const SizedBox(height: AppGap.section),
                if (allowance.onLastOne)
                  LastFreeValuationCard(onSignIn: () {}),
              ],
            ),
          ),
        ],
      );
}

/// Google's mark, at the size it ships, in both themes.
class _ButtonPage extends StatelessWidget {
  const _ButtonPage();

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Google sign-in button',
                style: context.text.headlineMedium),
            const SizedBox(height: AppGap.heading),
            Text(
              "Google's supplied mark, unaltered: no recolouring, no "
              'redrawing, only scaling. Their colours for the light and '
              'dark variants, their wording, and the pill shape - one of '
              'the two their guidelines sanction.',
              style: context.text.bodySmall?.copyWith(height: 1.5),
            ),
            const SizedBox(height: AppGap.section),
            SignInWithGoogleButton(onPressed: () {}),
            const SizedBox(height: AppGap.block),
            const SignInWithGoogleButton(busy: true, onPressed: null),
          ],
        ),
      );
}
