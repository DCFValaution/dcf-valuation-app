/// The screen after the last free valuation.
///
/// NEVER A DEAD END. That is the whole specification of this screen, and
/// it is why it has three states rather than one: signing in can fail, and
/// a reader who is offline cannot sign in at all. In every state there are
/// two ways onward that need no network - open something already valued,
/// or go back - and the sign-in button is never the only thing on screen.
///
/// It does not decide anything. It is shown because the SERVER returned a
/// 403, and the numbers on it come from that response. A screen that
/// decided when to appear would be a screen that could be told not to.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'account_service.dart';
import 'google_sign_in_button.dart';
import 'release_log.dart';
import 'theme.dart';
import 'valuation_api.dart';

class ValuationGateView extends StatefulWidget {
  const ValuationGateView({
    super.key,
    required this.gated,
    required this.service,
    this.onSignedIn,
    this.onOpenSaved,
    this.onBack,
  });

  /// The server's refusal, as it arrived.
  final ValuationGated gated;

  /// Null in a build with no Firebase, where signing in is not on offer.
  /// The screen still works: the ways out do not need an account.
  final AccountService? service;

  /// Called after a successful sign-in, so the caller can retry.
  final VoidCallback? onSignedIn;

  final VoidCallback? onOpenSaved;
  final VoidCallback? onBack;

  @override
  State<ValuationGateView> createState() => _ValuationGateViewState();
}

class _ValuationGateViewState extends State<ValuationGateView> {
  bool _busy = false;
  String? _error;
  bool _offline = false;

  Future<void> _signIn() async {
    final service = widget.service;
    if (service == null) {
      setState(() {
        _error = 'Signing in is unavailable in this build.';
        _offline = false;
      });
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _offline = false;
    });
    try {
      await service.signIn().timeout(const Duration(seconds: 60));
      if (!mounted) return;
      setState(() => _busy = false);
      widget.onSignedIn?.call();
    } catch (e, stack) {
      if (!mounted) return;
      // Changing their mind is not a failure and gets no red box.
      if (e is AccountError && e.message == cancelled) {
        setState(() => _busy = false);
        return;
      }
      logFailure('gate sign-in', e, stack);
      setState(() {
        _busy = false;
        _offline = _looksOffline(e);
        // NOT "Sign-in did not finish" - that is the heading directly
        // above it, and a body that repeats its own title reads as a
        // machine talking.
        _error = _offline
            ? null
            : (e is AccountError ? e.message : 'Nothing has changed.');
      });
    }
  }

  /// Whether this failure is "no connection" rather than "it went wrong".
  ///
  /// Worth separating because the advice differs completely: one is "try
  /// again", the other is "you cannot do this until you are back online,
  /// and here is what still works".
  ///
  /// BY MESSAGE, NOT BY TYPE, and both halves of that have been wrong
  /// before:
  ///
  ///   `error is SocketException` - names a `dart:io` type, and this
  ///       screen is built for the web too, where dart:io does not exist.
  ///
  ///   `error.runtimeType.toString() == 'SocketException'` - looks
  ///       platform-independent and is worse, because it only works in a
  ///       DEBUG build. dart2js minifies class names, so in the release
  ///       build that reaches readers this compares against something
  ///       like "a1" and never matches. Every test passed; the release
  ///       web build showed the wrong notice. It took a screenshot of a
  ///       real release build to see it.
  ///
  /// Exception messages are built from string literals, and literals
  /// survive minification - so matching the text is the one approach that
  /// behaves the same in a test, in a debug build and in production.
  static bool _looksOffline(Object error) {
    // A TIMEOUT IS NOT EVIDENCE OF BEING OFFLINE. Sign-in waits sixty
    // seconds; a reader who leaves the Google screen open, or whose
    // provider is simply slow, hits that on a perfectly good connection -
    // and "You are offline" is then a false statement about their machine
    // that sends them off to check their wifi. It falls through to the
    // ordinary failure notice, which offers a retry.
    final text = error.toString().toLowerCase();
    return text.contains('socketexception') ||
        text.contains('clientexception') ||
        text.contains('network') ||
        text.contains('offline') ||
        text.contains('unreachable') ||
        text.contains('failed host lookup');
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final gated = widget.gated;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _UsedUp(gated: gated),
        const SizedBox(height: AppGap.section),

        Text(
          gated.isNetworkLimit ? 'Sign in to carry on' : 'Carry on for free',
          key: const Key('gate-headline'),
          style: context.text.headlineMedium,
        ),
        const SizedBox(height: AppGap.heading),
        Text(
          gated.message,
          key: const Key('gate-message'),
          style: context.text.bodyMedium?.copyWith(
            color: colors.textSecondary,
            height: 1.55,
          ),
        ),
        const SizedBox(height: AppGap.block),

        if (_offline)
          _Notice(
            key: const Key('gate-offline'),
            icon: Icons.wifi_off_rounded,
            tint: colors.cautionSurface,
            fg: colors.caution,
            title: 'You are offline',
            // NOT "anything you have already valued still opens".
            //
            // It does not. Firestore persistence is not enabled, so the
            // web has no offline cache at all; reopening a save re-values
            // through the API, which needs the network on every platform;
            // and the reader seeing this gate is anonymous, so the rules
            // mean they have no saves in the first place. It promised the
            // impossible to exactly the people who would read it.
            body: 'Nothing has changed. Try again when you are back '
                'online.',
          ),
        if (_error != null)
          _Notice(
            key: const Key('gate-error'),
            icon: Icons.error_outline_rounded,
            tint: colors.negativeSurface,
            fg: colors.negative,
            title: 'Sign-in did not finish',
            // NOT "keep using what you have already valued". The reader
            // seeing this gate is anonymous, and the rules refuse an
            // anonymous installation a save - so there is nothing to keep
            // using. The one thing that IS true for them is the same-day
            // marker: reopening a company they valued today is free.
            body: '$_error You can try again, or reopen a company you '
                'valued today. That costs nothing.',
          ),
        if (_offline || _error != null) const SizedBox(height: AppGap.block),

        if (widget.service != null)
          SignInWithGoogleButton(busy: _busy, onPressed: _signIn),
        if (widget.service != null) const SizedBox(height: AppSpacing.md),

        // The ways out. Present in every state, including while a sign-in
        // is in flight, and neither of them touches the network.
        //
        // The saved-work button is offered ONLY when there is saved work.
        // An anonymous installation cannot keep a save - the Firestore
        // rules refuse it - so offering to open one would be a button
        // that leads to an empty screen, on the one screen in the app
        // whose entire job is not to be a dead end.
        if (widget.onOpenSaved != null) ...[
          SizedBox(
            height: 48,
            child: OutlinedButton.icon(
              key: const Key('gate-open-saved'),
              onPressed: widget.onOpenSaved,
              icon: const Icon(Icons.bookmark_border_rounded, size: 18),
              label: const Text('Open a saved valuation'),
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
        ] else ...[
          Text(
            'Opening a company you have already valued today costs nothing '
            '— the same company on the same day is never counted twice.',
            key: const Key('gate-reopen-is-free'),
            textAlign: TextAlign.center,
            style: context.text.labelSmall?.copyWith(height: 1.5),
          ),
          const SizedBox(height: AppSpacing.md),
        ],
        Center(
          child: TextButton(
            key: const Key('gate-back'),
            onPressed: widget.onBack,
            child: const Text('Back'),
          ),
        ),

        const SizedBox(height: AppGap.section),
        Text(
          gated.isNetworkLimit
              ? 'Signed-out valuations also have a daily cap per network, '
                  'and one address can be shared by a household, an office '
                  'or a whole campus. Signing in counts them as yours '
                  'instead.'
              : 'Why a limit at all: each valuation pulls company filings '
                  'and market prices. Signing in is free and lets us keep '
                  'that fair.',
          key: const Key('gate-why'),
          style: context.text.labelSmall?.copyWith(height: 1.5),
        ),
      ],
    );
  }
}

/// What has happened, counted rather than described.
class _UsedUp extends StatelessWidget {
  const _UsedUp({required this.gated});

  final ValuationGated gated;

  @override
  Widget build(BuildContext context) {
    final accent = context.scheme.primary;
    final limit = gated.limit;

    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: context.colors.accentSurface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: accent.withValues(alpha: 0.25)),
      ),
      child: Row(
        children: [
          // One tick per valuation used, up to a sensible number: at five
          // the row stops being countable at a glance and becomes
          // decoration, so past that the figure alone does the work.
          if (!gated.isNetworkLimit &&
              limit != null &&
              limit > 0 &&
              limit <= 5) ...[
            for (var i = 0; i < limit; i++) ...[
              Icon(Icons.check_circle_rounded, size: 20, color: accent),
              const SizedBox(width: 6),
            ],
          ] else
            Padding(
              padding: const EdgeInsets.only(right: AppSpacing.md),
              child: Icon(Icons.info_outline_rounded, size: 20, color: accent),
            ),
          Expanded(
            child: Text(
              // THE NETWORK CAP IS NOT THEIR ALLOWANCE, and saying
              // "20 of 20 valuations used today" to somebody who has made
              // none would be a plain falsehood. A reader on a Singapore
              // carrier or a school network can meet this cap before they
              // have valued a single company.
              gated.isNetworkLimit
                  ? 'Free valuations from this network are used up for today'
                  : limit == null
                      ? 'Free valuations used'
                      : gated.window == 'day'
                          ? '$limit of $limit valuations used today'
                          : '$limit of $limit free valuations used',
              key: const Key('gate-count'),
              style: context.text.titleSmall?.copyWith(
                color: accent,
                fontFeatures: const [],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({
    super.key,
    required this.icon,
    required this.tint,
    required this.fg,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final Color tint;
  final Color fg;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(AppSpacing.lg),
        decoration: BoxDecoration(
          color: tint,
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(color: fg.withValues(alpha: 0.35)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 20, color: fg),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: context.text.titleSmall?.copyWith(color: fg)),
                  const SizedBox(height: 2),
                  Text(body,
                      style: context.text.bodySmall
                          ?.copyWith(color: fg, height: 1.5)),
                ],
              ),
            ),
          ],
        ),
      );
}
