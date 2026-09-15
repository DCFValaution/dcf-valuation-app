import 'package:flutter/material.dart';

import 'theme.dart';
import 'ticker_search.dart';
import 'valuation_api.dart';

/// Matching companies beneath a search field.
///
/// Used by the main ticker search and by adding a peer, so both find
/// companies the same way. Every state says what the person can still do: the
/// dropdown is an aid to typing, never a gate.
class SearchDropdown extends StatelessWidget {
  const SearchDropdown({
    super.key = const Key('search-dropdown'),
    required this.controller,
    required this.onSelected,
    this.unavailableText =
        'Search is unavailable right now. You can still type a ticker '
        'and tap Value.',
    this.emptyText =
        'No matching companies. If you know the ticker, type it and '
        'tap Value.',
    this.maxHeight = 380,
    this.elevation = 6,
  });

  final TickerSearchController controller;
  final ValueChanged<CompanySearchResult> onSelected;

  /// What to say when the search could not run, and when nothing matched -
  /// each naming the fallback that applies where the dropdown is used.
  final String unavailableText;
  final String emptyText;
  final double maxHeight;
  final double elevation;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final results = controller.results;
    final loading = controller.phase == SearchPhase.loading;

    final Widget body;
    if (results.isNotEmpty) {
      body = ListView.separated(
        shrinkWrap: true,
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        itemCount: results.length,
        separatorBuilder: (_, _) =>
            Divider(height: 1, color: colors.hairline, indent: AppSpacing.lg),
        itemBuilder: (context, i) => _SearchResultRow(
          result: results[i],
          onTap: () => onSelected(results[i]),
        ),
      );
    } else if (loading) {
      body = const _SearchNotice(
        leading: SizedBox(
          width: 14,
          height: 14,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
        text: 'Searching…',
      );
    } else if (controller.phase == SearchPhase.unavailable) {
      body = _SearchNotice(
        icon: Icons.cloud_off_rounded,
        text: unavailableText,
      );
    } else {
      body = _SearchNotice(icon: Icons.search_off_rounded, text: emptyText);
    }

    return Material(
      elevation: elevation,
      shadowColor: Colors.black26,
      color: context.scheme.surface,
      borderRadius: BorderRadius.circular(AppRadius.control),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxHeight),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Subtle: a refinement is loading while the last answer stays
            // readable, rather than the list blinking out on every pause.
            SizedBox(
              height: 2,
              child: loading && results.isNotEmpty
                  ? const LinearProgressIndicator(minHeight: 2)
                  : null,
            ),
            Flexible(child: body),
          ],
        ),
      ),
    );
  }
}

class _SearchResultRow extends StatelessWidget {
  const _SearchResultRow({required this.result, required this.onTap});

  final CompanySearchResult result;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    result.name,
                    style: context.text.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (result.exchange.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      result.exchange,
                      style: context.text.labelSmall?.copyWith(
                        color: colors.textSecondary,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Container(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.sm,
                vertical: 3,
              ),
              decoration: BoxDecoration(
                color: colors.accentSurface,
                borderRadius: BorderRadius.circular(AppRadius.chip),
              ),
              child: Text(
                result.ticker,
                style: context.text.labelSmall?.copyWith(
                  color: context.scheme.primary,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SearchNotice extends StatelessWidget {
  const _SearchNotice({this.icon, this.leading, required this.text});

  final IconData? icon;
  final Widget? leading;
  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Padding(
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          leading ?? Icon(icon, size: 16, color: colors.textSecondary),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(
              text,
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
