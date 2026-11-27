/// The account: what is held, and how to get rid of it.
///
/// Google Play requires an in-app route to deletion for any app that
/// offers accounts. This is it, and the web page at `/delete` is the
/// second route the same policy requires, for somebody who has already
/// uninstalled.
///
/// The screen leads with WHAT IS HELD rather than with settings, because
/// that list is the whole of what signing in costs a person, and a
/// privacy policy nobody opens is not where it should first appear.
///
/// SIGNED OUT IS A SCREEN, NOT AN APOLOGY
/// --------------------------------------
/// It used to be one centred grey paragraph with no way to sign in - on
/// the screen called Account. It is now a page: what an account gives
/// you, Google's own button, and the reassurance that valuing companies
/// does not need one.
///
/// "Signed out" also no longer means `current == null`. Every
/// installation is signed in anonymously from first launch, because that
/// is what lets the free allowance be counted per installation rather
/// than per IP address. The question this screen asks is
/// [AccountServiceState.signedIn].
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import 'account_service.dart';
import 'excel_delivery.dart';
import 'google_sign_in_button.dart';
import 'release_log.dart';
import 'theme.dart';

class AccountScreen extends StatefulWidget {
  const AccountScreen({
    super.key,
    required this.service,
    this.watchlistCount = 0,
    this.savesCount = 0,
    this.onSignedOut,
  });

  final AccountService service;

  /// Shown in the deletion copy, so the consequence is counted rather
  /// than described: "your 6 companies and 3 saved valuations".
  final int watchlistCount;
  final int savesCount;

  final VoidCallback? onSignedOut;

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  bool _busy = false;
  bool _signingIn = false;
  String? _signOutError;
  String? _signInError;
  bool _left = false;
  bool _wasSignedIn = false;
  int? _counted;
  StreamSubscription<Account?>? _watch;

  @override
  void initState() {
    super.initState();
    _wasSignedIn = widget.service.signedIn;
    if (_wasSignedIn) _loadCount();

    // THE AUTH STATE IS THE AUTHORITY, not what signOut() returned.
    //
    // Without this the screen kept showing the account after it was
    // signed out or deleted - which, on the one screen whose job is to
    // remove it, is the app telling the person it did not work.
    //
    // The test is now a TRANSITION rather than a value. Signing out lands
    // on the anonymous account rather than on nothing, so `account ==
    // null` would never fire; and leaving whenever we are merely not
    // signed in would close this screen the instant a signed-out reader
    // opened it.
    _watch = widget.service.changes.listen((account) {
      if (!mounted) return;
      final signedIn = widget.service.signedIn;
      if (_wasSignedIn && !signedIn) {
        _leave();
        return;
      }
      if (!_wasSignedIn && signedIn) {
        _wasSignedIn = true;
        _loadCount();
      }
      setState(() {});
    });
  }

  Future<void> _loadCount() async {
    try {
      final counted = await widget.service.valuationsCounted();
      if (!mounted) return;
      setState(() => _counted = counted);
    } catch (e) {
      logFailure('account valuations counted', e);
    }
  }

  /// Leave the screen, once.
  ///
  /// Both the auth listener and the sign-out/delete handlers call this,
  /// and either may be first. Popping twice would take the screen behind
  /// this one with it.
  void _leave() {
    if (_left) return;
    _left = true;
    widget.onSignedOut?.call();
  }

  @override
  void dispose() {
    _watch?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      // NOTE: no backgroundColor override.
      //
      // This line used to read `colors.accentSurface.withValues(alpha:
      // 0.0)` - a fully transparent Scaffold - so nothing painted behind
      // the screen and the black below showed through. Pale cards on
      // black was the whole of why the screen looked broken. The theme
      // was always right; this screen had opted out of it.
      appBar: AppBar(title: const Text('Account')),
      body: widget.service.signedIn
          ? _signedIn(context, widget.service.current!)
          : _signedOut(context),
    );
  }

  // -------------------------------------------------------------------
  // Signed in
  // -------------------------------------------------------------------

  Widget _signedIn(BuildContext context, Account account) {
    final colors = context.colors;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.sm,
        AppSpacing.lg,
        AppSpacing.xxxl,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _profile(context, account),
          const SizedBox(height: AppGap.block),
          _plan(context),
          const SizedBox(height: AppGap.section),

          _label(context, 'What we hold'),
          _whatWeHold(context, account),
          const SizedBox(height: AppGap.section),

          _label(context, 'Your data'),
          _AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _actionRow(
                  context,
                  rowKey: const Key('account-export'),
                  first: true,
                  icon: Icons.download_rounded,
                  label: 'Download my data',
                  hint: 'A JSON file, to save or share',
                  onTap: _busy ? null : _export,
                ),
                _actionRow(
                  context,
                  rowKey: const Key('account-sign-out'),
                  icon: Icons.logout_rounded,
                  label: _busy ? 'Signing out…' : 'Sign out on this device',
                  hint: 'Your data stays on our servers',
                  busy: _busy,
                  onTap: _busy ? null : _signOut,
                ),
              ],
            ),
          ),
          if (_signOutError != null) _signOutFailure(context),
          const SizedBox(height: AppGap.section),

          _dangerLabel(context),
          const SizedBox(height: AppGap.block),
          _dangerZone(context, colors),
        ],
      ),
    );
  }

  Widget _profile(BuildContext context, Account account) {
    final colors = context.colors;
    final email = account.email;
    final initial = (email == null || email.isEmpty)
        ? '?'
        : email[0].toUpperCase();

    return _AppCard(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: colors.accentSurface,
                shape: BoxShape.circle,
              ),
              child: Text(
                initial,
                style: context.text.titleLarge?.copyWith(
                  color: context.scheme.primary,
                ),
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    email ?? 'Signed in',
                    key: const Key('account-email'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: context.text.titleMedium,
                  ),
                  const SizedBox(height: 2),
                  Text('Signed in with Google', style: context.text.bodySmall),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _plan(BuildContext context) {
    final colors = context.colors;
    return _AppCard(
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
        child: Row(
          children: [
            Icon(Icons.all_inclusive_rounded, size: 18, color: colors.positive),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Text(
                'Unlimited valuations',
                key: const Key('account-plan'),
                style: context.text.bodyLarge,
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.sm,
                vertical: 2,
              ),
              decoration: BoxDecoration(
                color: colors.positiveSurface,
                borderRadius: BorderRadius.circular(AppRadius.chip),
              ),
              child: Text(
                'FREE PLAN',
                style: context.text.labelMedium?.copyWith(
                  color: colors.positive,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// The list that is the point of this screen.
  Widget _whatWeHold(BuildContext context, Account account) {
    final rows = <(String, String)>[
      ('Google account identifier', 'Held, not shown'),
      ('Email address', account.email ?? '—'),
      (
        'Watchlist',
        '${widget.watchlistCount} '
            '${widget.watchlistCount == 1 ? 'company' : 'companies'}',
      ),
      (
        'Saved valuations',
        '${widget.savesCount} ${widget.savesCount == 1 ? 'save' : 'saves'}',
      ),
      // We count valuations now, so we say so. A number held and not
      // shown would make this whole list untrustworthy.
      ('Valuations counted', _counted == null ? '—' : '$_counted'),
      ('Timestamps', 'Created, last used'),
    ];

    return _AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final (i, (label, value)) in rows.indexed)
            _CardRow(
              first: i == 0,
              child: Row(
                children: [
                  Expanded(child: Text(label, style: context.text.bodySmall)),
                  const SizedBox(width: AppSpacing.md),
                  Text(
                    value,
                    style: context.text.bodySmall?.copyWith(
                      color: context.scheme.onSurface,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ),
          Container(
            width: double.infinity,
            color: context.scheme.surfaceContainerHighest,
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Text(
              'No password, no payment details, no analytics profile, no '
              'advertising identifier. Nothing is sold, and nothing goes '
              'anywhere except the services that run the app — Google '
              'Firebase, which stores it, and Render, which hosts it.',
              key: const Key('account-nothing-else'),
              style: context.text.labelSmall?.copyWith(height: 1.45),
            ),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Signed out
  // -------------------------------------------------------------------

  Widget _signedOut(BuildContext context) {
    final colors = context.colors;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.xl,
        AppSpacing.lg,
        AppSpacing.xxxl,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            width: 56,
            height: 56,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: colors.accentSurface,
              borderRadius: BorderRadius.circular(AppRadius.control),
            ),
            child: Icon(
              Icons.bookmark_added_outlined,
              size: 28,
              color: context.scheme.primary,
            ),
          ),
          const SizedBox(height: AppGap.block),
          Text('Keep your work', style: context.text.headlineMedium),
          const SizedBox(height: AppGap.heading),
          Text(
            'An account adds a watchlist and saved valuations, kept against '
            'your Google sign-in so they are still there on your next '
            'device.',
            key: const Key('account-signed-out'),
            style: context.text.bodyMedium?.copyWith(
              color: colors.textSecondary,
              height: 1.55,
            ),
          ),
          const SizedBox(height: AppGap.section),

          if (_signInError != null) ...[
            Container(
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: colors.negativeSurface,
                borderRadius: BorderRadius.circular(AppRadius.card),
                border: Border.all(
                  color: colors.negative.withValues(alpha: .35),
                ),
              ),
              child: Text(
                _signInError!,
                key: const Key('account-sign-in-error'),
                style: context.text.bodySmall?.copyWith(
                  color: colors.negative,
                  height: 1.5,
                ),
              ),
            ),
            const SizedBox(height: AppGap.block),
          ],

          SignInWithGoogleButton(busy: _signingIn, onPressed: _signIn),
          const SizedBox(height: AppSpacing.md),
          Center(
            child: Text(
              // The copy says the number, because the number is the
              // product promise: you can try this before anybody asks you
              // for anything.
              'Your first 3 valuations work without an account.',
              key: const Key('account-free-line'),
              style: context.text.labelSmall,
            ),
          ),
          const SizedBox(height: AppGap.section),

          _AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final (i, (icon, label)) in const [
                  (Icons.star_border_rounded, 'A watchlist of companies'),
                  (Icons.bookmark_border_rounded, 'Valuations you can reopen'),
                  (Icons.all_inclusive_rounded, 'Unlimited valuations'),
                ].indexed)
                  _CardRow(
                    first: i == 0,
                    child: Row(
                      children: [
                        Icon(icon, size: 20, color: colors.textSecondary),
                        const SizedBox(width: AppSpacing.md),
                        Expanded(
                          child: Text(label, style: context.text.bodyMedium),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: AppGap.block),
          Text(
            'We ask Google only for your email address and an account '
            'identifier. No password reaches us.',
            style: context.text.labelSmall?.copyWith(height: 1.45),
          ),
        ],
      ),
    );
  }

  Future<void> _signIn() async {
    setState(() {
      _signingIn = true;
      _signInError = null;
    });
    try {
      await widget.service.signIn().timeout(const Duration(seconds: 60));
      if (!mounted) return;
      setState(() => _signingIn = false);
    } catch (e, stack) {
      if (!mounted) return;
      if (e is AccountError && e.message == cancelled) {
        setState(() => _signingIn = false);
        return;
      }
      logFailure('account sign-in', e, stack);
      setState(() {
        _signingIn = false;
        _signInError = e is AccountError
            ? e.message
            : 'Signing in did not finish. Please try again.';
      });
    }
  }

  // -------------------------------------------------------------------
  // Shared pieces
  // -------------------------------------------------------------------

  Widget _label(BuildContext context, String text) => Padding(
    padding: const EdgeInsets.only(left: AppSpacing.xs, bottom: AppGap.heading),
    child: Text(text.toUpperCase(), style: context.text.labelMedium),
  );

  Widget _dangerLabel(BuildContext context) {
    final colors = context.colors;
    return Row(
      children: [
        Expanded(child: Divider(color: colors.hairline)),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          child: Text(
            'DANGER ZONE',
            style: context.text.labelMedium?.copyWith(color: colors.negative),
          ),
        ),
        Expanded(child: Divider(color: colors.hairline)),
      ],
    );
  }

  /// What went wrong, and a way to try it again.
  Widget _signOutFailure(BuildContext context) {
    final colors = context.colors;
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Row(
        children: [
          Expanded(
            child: Text(
              _signOutError!,
              key: const Key('account-sign-out-error'),
              style: context.text.bodySmall?.copyWith(
                color: colors.negative,
                height: 1.4,
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          TextButton(
            key: const Key('account-sign-out-retry'),
            onPressed: _busy ? null : _signOut,
            child: const Text('Try again'),
          ),
        ],
      ),
    );
  }

  Widget _actionRow(
    BuildContext context, {
    required Key rowKey,
    required IconData icon,
    required String label,
    required String hint,
    VoidCallback? onTap,
    bool first = false,
    bool busy = false,
  }) => _CardRow(
    first: first,
    onTap: onTap,
    rowKey: rowKey,
    child: Row(
      children: [
        if (busy)
          const SizedBox.square(
            dimension: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        else
          Icon(icon, size: 20, color: context.colors.textSecondary),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: context.text.bodyLarge),
              Text(hint, style: context.text.labelSmall),
            ],
          ),
        ),
        Icon(
          Icons.chevron_right_rounded,
          size: 20,
          color: context.colors.textSecondary,
        ),
      ],
    ),
  );

  Widget _dangerZone(BuildContext context, AppColors colors) {
    final counted = widget.watchlistCount == 0 && widget.savesCount == 0
        ? 'your account'
        : 'your account, your ${widget.watchlistCount} watchlist '
              '${widget.watchlistCount == 1 ? 'company' : 'companies'} and your '
              '${widget.savesCount} saved '
              '${widget.savesCount == 1 ? 'valuation' : 'valuations'}';

    return _AppCard(
      tint: colors.negativeSurface,
      border: colors.negative.withValues(alpha: 0.35),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'Delete account',
              style: context.text.titleMedium?.copyWith(color: colors.negative),
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Removes $counted from our servers within 30 days. It cannot '
              'be undone, and the app keeps working without an account.',
              key: const Key('account-delete-consequences'),
              style: context.text.bodySmall?.copyWith(
                color: colors.negative,
                height: 1.5,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            SizedBox(
              height: 48,
              child: FilledButton(
                key: const Key('account-delete'),
                onPressed: _busy ? null : _confirmDelete,
                style: FilledButton.styleFrom(
                  backgroundColor: colors.negative,
                  foregroundColor: Colors.white,
                ),
                child: const Text('Delete account and all data'),
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            Text(
              'You can also do this without the app, at '
              'intrinsic-web.onrender.com/delete',
              style: context.text.labelSmall?.copyWith(
                color: colors.negative,
                height: 1.45,
              ),
            ),
          ],
        ),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------

  /// A FILE, not the clipboard.
  ///
  /// It used to put the JSON on the clipboard, which is the wrong shape
  /// for the thing: an export of everything we hold can be thousands of
  /// characters, a clipboard holds one thing at a time and loses it to
  /// the next copy, and on Android a long clipboard write is silently
  /// truncated by some keyboards. It goes through the same delivery path
  /// as the workbook export - share sheet on Android, download in the
  /// browser - so there is one way files leave this app.
  Future<void> _export() async {
    setState(() => _busy = true);
    try {
      final data = await widget.service.exportData();
      final json = const JsonEncoder.withIndent('  ').convert(data);
      final outcome = await deliverWorkbook(
        bytes: utf8.encode(json),
        filename: 'intrinsic-my-data.json',
        ticker: 'Intrinsic',
        mimeType: 'application/json',
        subject: 'My Intrinsic data',
        text: 'Everything Intrinsic holds about me.',
        announce: _say,
      );
      switch (outcome) {
        case DeliveryDone(:final message?):
          _say(message);
        case DeliveryInstruction(:final message):
        case DeliveryFailure(:final message):
          _say(message);
        case DeliveryDone():
        case DeliveryCancelled():
          break;
      }
    } catch (e) {
      _say(_message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Sign out, visibly.
  ///
  /// The first version was four lines with no busy flag, no try and no
  /// catch: `await service.signOut()` hung on the web, so the row showed
  /// its tap animation and then nothing - no spinner, no message, and
  /// nothing in the console. A button that silently does nothing is
  /// indistinguishable from a broken app.
  Future<void> _signOut() async {
    setState(() {
      _busy = true;
      _signOutError = null;
    });
    try {
      await widget.service.signOut().timeout(const Duration(seconds: 10));
      logStep('sign-out', 'screen: service returned');
      if (!mounted) return;
      setState(() => _busy = false);
      _leave();
    } catch (e, stack) {
      logFailure('sign-out', e, stack);
      if (!mounted) return;
      // One check before calling it a failure: a thrown error does not
      // mean the person is still signed in. If the auth state says they
      // are gone, they are gone.
      if (!widget.service.signedIn) {
        setState(() => _busy = false);
        _leave();
        return;
      }
      setState(() {
        _busy = false;
        _signOutError = e is TimeoutException
            ? 'Signing out is taking too long. Please try again.'
            : 'Signing out did not finish. Please try again.';
      });
    }
  }

  /// Typed confirmation, not a yes/no.
  Future<void> _confirmDelete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => const _TypeToConfirmDialog(),
    );
    if (confirmed != true) return;
    if (!mounted) return;

    // Captured BEFORE the await: the auth listener leaves this screen the
    // instant the user is gone, so by the time the delete returns, this
    // widget's own context may be on its way out. The messenger above it
    // is not.
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await widget.service.deleteAccount();
      if (!mounted) return;
      setState(() => _busy = false);
      messenger.showSnackBar(
        const SnackBar(
          content: Text('Your account and everything in it have been deleted.'),
        ),
      );
      _leave();
    } catch (e, stack) {
      logFailure('delete-account', e, stack);
      if (!mounted) return;
      setState(() => _busy = false);
      // The message, as thrown, and nothing appended. Only the thing that
      // ran the deletion knows how far it got, so only it gets to say.
      messenger.showSnackBar(SnackBar(content: Text(_message(e))));
    }
  }

  static String _message(Object e) =>
      e is AccountError ? e.message : 'Something went wrong.';

  void _say(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }
}

/// The app's card, stated here rather than taken from `cardTheme`, because
/// two of the three on this screen are tinted and the third must match them
/// exactly.
class _AppCard extends StatelessWidget {
  const _AppCard({required this.child, this.tint, this.border});

  final Widget child;
  final Color? tint;
  final Color? border;

  @override
  Widget build(BuildContext context) => Container(
    decoration: BoxDecoration(
      color: tint ?? context.scheme.surface,
      borderRadius: BorderRadius.circular(AppRadius.card),
      border: Border.all(color: border ?? context.colors.hairline),
    ),
    clipBehavior: Clip.antiAlias,
    child: child,
  );
}

/// One row in a card of rows, hairline-divided from the one above.
class _CardRow extends StatelessWidget {
  const _CardRow({
    required this.child,
    this.first = false,
    this.onTap,
    this.rowKey,
  });

  final Widget child;
  final bool first;
  final VoidCallback? onTap;
  final Key? rowKey;

  @override
  Widget build(BuildContext context) {
    final row = Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: child,
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!first)
          const Divider(
            height: 1,
            indent: AppSpacing.lg,
            endIndent: AppSpacing.lg,
          ),
        if (onTap == null)
          KeyedSubtree(key: rowKey, child: row)
        else
          InkWell(key: rowKey, onTap: onTap, child: row),
      ],
    );
  }
}

/// Type the word, do not tap a button.
///
/// A dialog with two buttons is dismissed by muscle memory, and this one
/// cannot be undone. The word being typed is the word for what happens.
class _TypeToConfirmDialog extends StatefulWidget {
  const _TypeToConfirmDialog();

  @override
  State<_TypeToConfirmDialog> createState() => _TypeToConfirmDialogState();
}

class _TypeToConfirmDialogState extends State<_TypeToConfirmDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  bool get _matches => _controller.text.trim().toUpperCase() == 'DELETE';

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Delete everything?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text('This cannot be undone. Type DELETE to confirm.'),
          const SizedBox(height: AppSpacing.lg),
          TextField(
            key: const Key('account-delete-confirm-field'),
            controller: _controller,
            autocorrect: false,
            decoration: const InputDecoration(hintText: 'DELETE'),
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('account-delete-final'),
          onPressed: _matches ? () => Navigator.of(context).pop(true) : null,
          child: const Text('Delete'),
        ),
      ],
    );
  }
}
