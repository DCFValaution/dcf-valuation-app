/// What the reader is told about the free allowance, before the gate.
///
/// Two things, and the restraint between them is the design:
///
///   [FreeCountPill]            a hairline pill in the header, at label
///                              weight. The whole of it, while valuations
///                              remain.
///   [LastFreeValuationCard]    on the LAST one only. The single moment the
///                              count earns any emphasis.
///
/// Both are courtesies. Neither decides anything: the numbers come from
/// response headers the server sets, and the refusal, when it comes, is a
/// 403 that the server alone chooses to send.
library;

import 'package:flutter/material.dart';

import 'allowance.dart';
import 'theme.dart';

class FreeCountPill extends StatelessWidget {
  const FreeCountPill({super.key, required this.allowance});

  final Allowance allowance;

  @override
  Widget build(BuildContext context) {
    final left = allowance.remaining ?? 0;
    return Container(
      key: const Key('free-count-pill'),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: 5,
      ),
      decoration: BoxDecoration(
        color: context.scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppRadius.chip),
        border: Border.all(color: context.colors.hairline),
      ),
      child: Text(
        // Singular when there is one, because "1 free valuations left" is
        // the kind of thing that makes a reader trust the numbers less.
        left == 1 ? '1 free valuation left' : '$left free valuations left',
        style: context.text.labelSmall?.copyWith(fontWeight: FontWeight.w600),
      ),
    );
  }
}

class LastFreeValuationCard extends StatelessWidget {
  const LastFreeValuationCard({super.key, this.onSignIn});

  /// Null when there is no account system in this build, in which case the
  /// card still says what is happening and simply offers nothing.
  final VoidCallback? onSignIn;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Container(
      key: const Key('last-free-valuation'),
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: colors.cautionSurface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.caution.withValues(alpha: 0.3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.info_outline_rounded, size: 20, color: colors.caution),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'One free valuation left',
                  style: context.text.titleSmall?.copyWith(
                    color: colors.caution,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  'After that, signing in with Google keeps it going — '
                  'free, and it saves your work too.',
                  style: context.text.bodySmall?.copyWith(
                    color: colors.caution,
                    height: 1.5,
                  ),
                ),
                if (onSignIn != null) ...[
                  const SizedBox(height: AppSpacing.sm),
                  TextButton(
                    key: const Key('last-free-sign-in'),
                    onPressed: onSignIn,
                    style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      foregroundColor: colors.caution,
                      minimumSize: const Size(0, 32),
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                    child: const Text('Sign in now'),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
