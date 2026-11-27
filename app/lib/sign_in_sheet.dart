/// The only place the app asks who you are.
///
/// It opens on the SAVE action and nowhere else — never on launch, never
/// on valuing a company, never on exporting the spreadsheet. That is the
/// whole bargain of optional accounts, and it is a property worth a test
/// rather than a convention worth a comment: [requireAccount] is the one
/// function that can open this, and it is called from the save paths only.
///
/// Declining costs nothing. The sheet closes, the figure is still on
/// screen, and nothing was lost — so "Not now" is a plain text button
/// rather than a reluctant link, and it does not apologise.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'account_service.dart';
import 'release_log.dart';
import 'theme.dart';

/// Ask the reader to sign in, because they asked to save something.
///
/// Returns the account if they did, null if they declined or it failed.
/// Callers treat null as "carry on without saving", never as an error.
Future<Account?> requireAccount(
  BuildContext context,
  AccountService? service, {
  required String reason,
}) async {
  if (service == null) {
    // Firebase did not start. Saying "signing in is unavailable" is
    // honest; pretending the button did nothing is not.
    //
    // Logged at the moment of the tap as well as at startup, because the
    // two are minutes apart: somebody reporting this has the snackbar in
    // front of them and no reason to go looking for a line written
    // before the first frame.
    logStep('sign-in', 'refused: no account service - Firebase did not '
        'start. The reason was logged at startup.');
    _say(context, 'Signing in is unavailable just now. Everything else in '
        'the app still works.');
    return null;
  }
  // `signedIn`, not `current != null`.
  //
  // Every installation is signed in anonymously now, so `current` is
  // almost never null - and this line would have returned the anonymous
  // account to a caller asking for a real one, which means saving a
  // valuation would have silently done nothing.
  if (service.signedIn) return service.current;

  final signedIn = await showModalBottomSheet<bool>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (sheetContext) => _SignInSheet(service: service, reason: reason),
  );
  return signedIn == true ? service.current : null;
}

void _say(BuildContext context, String message) {
  if (!context.mounted) return;
  ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(message)));
}

class _SignInSheet extends StatefulWidget {
  const _SignInSheet({required this.service, required this.reason});

  final AccountService service;

  /// What they were trying to do, in the sheet's own words: "Save this
  /// valuation", "Add NVDA to your watchlist". The sheet is an
  /// interruption, so it says what it interrupted.
  final String reason;

  @override
  State<_SignInSheet> createState() => _SignInSheetState();
}

class _SignInSheetState extends State<_SignInSheet> {
  bool _busy = false;
  String? _error;
  StreamSubscription<Account?>? _watch;

  @override
  void initState() {
    super.initState();
    // The authority on whether somebody is signed in is the auth state,
    // not what `signIn()` returned.
    //
    // A pop-up result can fail to reach the app - severed by COOP, lost
    // to a redirect, or thrown away by a later error - while Firebase Auth
    // has in fact signed the person in. The sheet then sat there asking
    // them to sign in while they already were, and a reload proved it.
    //
    // So the sheet watches, and closes the moment there is an account,
    // whichever path produced one.
    _watch = widget.service.changes.listen((account) {
      if (widget.service.signedIn && mounted) Navigator.of(context).pop(true);
    });
    if (widget.service.signedIn) {
      // Already signed in before the sheet even opened - a redirect
      // coming back, most often. Pop after the first frame, because
      // popping during a build is not allowed.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) Navigator.of(context).pop(true);
      });
    }
  }

  @override
  void dispose() {
    _watch?.cancel();
    super.dispose();
  }

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.service.signIn();
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      // EVERYTHING, not just AccountError.
      //
      // The first version caught only AccountError, and google_sign_in's
      // web implementation throws UnimplementedError from authenticate().
      // It escaped the handler, `_busy` was never cleared, and the button
      // read "Signing in…" for as long as anyone was willing to wait -
      // with no Google window and nothing on screen to say why. A spinner
      // that cannot stop is the worst failure mode a button has, because
      // it is indistinguishable from slowness.
      if (!mounted) return;
      if (e is AccountError && e.message == cancelled) {
        // They changed their mind inside Google's own window. The app has
        // nothing to say about that.
        setState(() => _busy = false);
        return;
      }
      // One last check before calling it a failure: the auth state is
      // the authority, and a thrown error does not mean nobody got signed
      // in. This is the case that made the app say "did not finish" to
      // somebody who was, in fact, finished.
      if (widget.service.signedIn) {
        Navigator.of(context).pop(true);
        return;
      }
      setState(() {
        _busy = false;
        _error = e is AccountError
            ? e.message
            : 'Signing in did not finish. Please try again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xxl, 0, AppSpacing.xxl, AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              widget.reason,
              key: const Key('sign-in-reason'),
              style: context.text.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Signing in keeps your watchlist and your own assumptions on '
              'every device. Everything else in the app works without an '
              'account.',
              style: context.text.bodyMedium
                  ?.copyWith(color: colors.textSecondary, height: 1.5),
            ),
            const SizedBox(height: AppSpacing.xl),
            FilledButton.icon(
              key: const Key('sign-in-google'),
              onPressed: _busy ? null : _signIn,
              icon: _busy
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.login_rounded, size: 18),
              label: Text(_busy ? 'Signing in…' : 'Continue with Google'),
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(52),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.md),
              Text(
                _error!,
                key: const Key('sign-in-error'),
                style: context.text.bodySmall?.copyWith(color: colors.negative),
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            Container(
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: colors.accentSurface,
                borderRadius: BorderRadius.circular(AppRadius.control),
              ),
              child: Text(
                'We keep your Google account ID, your email address and what '
                'you save. No password ever reaches us. You can delete all '
                'of it from Account at any time.',
                style: context.text.bodySmall
                    ?.copyWith(color: colors.textSecondary, height: 1.45),
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            TextButton(
              key: const Key('sign-in-not-now'),
              onPressed: _busy ? null : () => Navigator.of(context).pop(false),
              style: TextButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
              ),
              child: const Text('Not now'),
            ),
          ],
        ),
      ),
    );
  }
}
