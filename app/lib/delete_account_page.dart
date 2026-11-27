/// The web page Google Play requires, and the path that reaches it.
///
/// Play's User Data policy says an app that lets people create an account
/// must also let them delete it, and that there must be a way to ask from
/// OUTSIDE the app - a working web link, relevant in scope, naming the app
/// or the developer. Somebody who has already uninstalled must still be
/// able to get their data removed, and asking them to reinstall in order
/// to ask is exactly what the rule exists to prevent.
///
/// It is a route inside the web build rather than a service of its own.
/// `render.yaml` already rewrites every path to `index.html`, so `/delete`
/// costs no new deployment, no new host and no DNS. The path is a constant
/// so that moving to a custom domain later is a DNS change and nothing
/// else: the route travels with the app.
library;

import 'package:flutter/material.dart';

import 'theme.dart';

/// Where the deletion page lives. Named once, because Play Console, the
/// privacy policy and the app itself all have to agree on it.
const String kDeletePath = '/delete';

/// Whether [path] asks for the deletion page.
///
/// Tolerant of the trailing slash and of case, because this URL gets typed
/// by hand off a privacy policy more often than it gets clicked.
bool isDeleteRoute(String path) {
  final trimmed = path.trim().toLowerCase();
  final withoutSlash = trimmed.endsWith('/') && trimmed.length > 1
      ? trimmed.substring(0, trimmed.length - 1)
      : trimmed;
  return withoutSlash == kDeletePath;
}

/// What the deletion page is currently showing.
enum DeletionStage {
  /// Nobody is signed in on this device. The page asks them to.
  signedOut,

  /// Signed in, and the consequences have not been accepted yet.
  ready,

  /// The request is with the server.
  working,

  /// It is done. Said plainly, because a silent success on a destructive
  /// action leaves a person wondering whether to do it again.
  done,

  /// It failed, and saying so is the only honest option: a person who
  /// believes their data is gone when it is not has been misled about the
  /// one thing this page exists to do.
  failed,
}

/// The deletion page.
///
/// Stateless and driven entirely by [stage], so every state is reachable in
/// a test and on a device without arranging an account first.
class DeleteAccountPage extends StatelessWidget {
  const DeleteAccountPage({
    super.key,
    required this.stage,
    this.email,
    this.error,
    this.onSignIn,
    this.onDelete,
    this.onBackToApp,
  });

  final DeletionStage stage;

  /// Whose account is about to go. Shown because this page is reached from
  /// a link in an email or a policy, and a person who has two Google
  /// accounts needs to see which one they signed into.
  final String? email;

  final String? error;

  final VoidCallback? onSignIn;
  final VoidCallback? onDelete;
  final VoidCallback? onBackToApp;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AppSpacing.xxl),
            child: ConstrainedBox(
              // A reading measure, not a phone width: this page is opened
              // on a laptop as often as on a device.
              constraints: const BoxConstraints(maxWidth: 560),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Delete your Intrinsic account',
                    key: const Key('delete-title'),
                    style: context.text.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  ..._body(context),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  List<Widget> _body(BuildContext context) {
    switch (stage) {
      case DeletionStage.signedOut:
        return [
          _paragraph(
            context,
            'Sign in with the Google account you used in the app. We need '
            'to know whose account to delete, and signing in is the only '
            'way to be sure it is yours.',
          ),
          const SizedBox(height: AppSpacing.lg),
          _consequences(context),
          const SizedBox(height: AppSpacing.xl),
          FilledButton(
            key: const Key('delete-sign-in'),
            onPressed: onSignIn,
            child: const Text('Continue with Google'),
          ),
          // A sign-in that failed has to say so HERE, not only once the
          // deletion itself fails. Without this the button appeared to do
          // nothing at all, which on the page somebody reached from a
          // privacy policy is the worst possible silence.
          if (error != null) ...[
            const SizedBox(height: AppSpacing.md),
            _paragraph(context, error!, color: context.colors.negative),
          ],
        ];

      case DeletionStage.ready:
      case DeletionStage.working:
        return [
          _paragraph(
            context,
            email == null
                ? 'You are signed in.'
                : 'Signed in as $email. This will delete that account.',
          ),
          const SizedBox(height: AppSpacing.lg),
          _consequences(context),
          const SizedBox(height: AppSpacing.xl),
          FilledButton(
            key: const Key('delete-confirm'),
            onPressed: stage == DeletionStage.working ? null : onDelete,
            style: FilledButton.styleFrom(
              backgroundColor: context.colors.negative,
            ),
            child: Text(
              stage == DeletionStage.working
                  ? 'Deleting…'
                  : 'Delete my account and all data',
            ),
          ),
        ];

      case DeletionStage.done:
        return [
          _paragraph(
            context,
            'Your account is gone, along with your watchlist and your saved '
            'valuations. They have been removed from the app, and will be '
            'gone from our backups within 30 days.',
          ),
          const SizedBox(height: AppSpacing.md),
          _paragraph(
            context,
            // "Nothing you valued was ever stored anyway" was true when
            // this page was written and is not any more: the free-
            // valuation counter records how many valuations were made and
            // which company on which day. It has been deleted along with
            // everything else - which is the honest thing to say, rather
            // than claiming it never existed.
            'Intrinsic still works without an account: your first three '
            'valuations need no sign-in. The count of what you valued has '
            'been deleted with the rest.',
          ),
          const SizedBox(height: AppSpacing.xl),
          OutlinedButton(
            key: const Key('delete-back'),
            onPressed: onBackToApp,
            child: const Text('Back to Intrinsic'),
          ),
        ];

      case DeletionStage.failed:
        return [
          // WHAT was removed is not this page's to assert.
          //
          // It used to say "Nothing has been removed" after every
          // failure. Documents are deleted before the auth user, so a
          // failure at the second step leaves the data already gone -
          // and this page would have said otherwise, about an action that
          // cannot be undone. The error below comes from the service,
          // which is the only thing that knows how far it got.
          _paragraph(
            context,
            'We could not delete the account.',
            color: context.colors.negative,
          ),
          if (error != null) ...[
            const SizedBox(height: AppSpacing.sm),
            _paragraph(context, error!, small: true),
          ],
          const SizedBox(height: AppSpacing.md),
          _paragraph(
            context,
            'Please try again. A failed attempt changes nothing beyond '
            'what the line above says.',
          ),
          const SizedBox(height: AppSpacing.xl),
          FilledButton(
            key: const Key('delete-retry'),
            onPressed: onDelete,
            child: const Text('Try again'),
          ),
        ];
    }
  }

  /// Said before the button, every time, in both the signed-out and the
  /// signed-in state. A consequence disclosed only after the irreversible
  /// action is not a disclosure.
  Widget _consequences(BuildContext context) {
    final colors = context.colors;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: colors.cautionSurface,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'What this removes',
            style: context.text.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
              color: colors.caution,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          for (final line in const [
            'Your account and the email address attached to it',
            'Every company on your watchlist',
            'Every valuation you saved, with its assumptions',
          ])
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text(
                '•  $line',
                style: context.text.bodySmall?.copyWith(color: colors.caution),
              ),
            ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'It cannot be undone. Intrinsic keeps working without an '
            'account.',
            style: context.text.bodySmall?.copyWith(
              color: colors.caution,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _paragraph(BuildContext context, String text,
      {Color? color, bool small = false}) {
    final style = small ? context.text.bodySmall : context.text.bodyMedium;
    return Text(
      text,
      style: style?.copyWith(
        color: color ?? context.colors.textSecondary,
        height: 1.5,
      ),
    );
  }
}
