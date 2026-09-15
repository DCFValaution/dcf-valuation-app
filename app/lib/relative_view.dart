/// The relative view: how the market prices comparable companies, set beside
/// the intrinsic value as a second opinion.
///
/// It is a different kind of answer from everything else in the app, and the
/// risk is that it gets read as the same kind - a second, perhaps better,
/// "true value". So:
///
///   * it is never called intrinsic value, and it is badged market-based
///     before any number appears;
///   * the intrinsic value is always shown with it, including when no relative
///     figure could be produced, because the relative view only means anything
///     beside it;
///   * the backend's own statement that a gap between the two is information
///     rather than an error is shown verbatim, as is its note that a relative
///     figure inherits the market's mispricing;
///   * a decline - no comparable peers, too few multiples - is presented as a
///     considered answer with its reasons, not as a failure.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'formatting.dart';
import 'relative_controller.dart';
import 'search_dropdown.dart';
import 'theme.dart';
import 'ticker_search.dart';
import 'ui.dart';
import 'valuation_api.dart';

String _money(double v) => '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';
String _multiple(double v) => '${v.toStringAsFixed(1)}x';

/// "P/E", "P/E and Price/Book", "P/E, EV/EBITDA and Price/Sales".
String joinWithAnd(List<String> items) => switch (items.length) {
  0 => '',
  1 => items.first,
  _ => '${items.sublist(0, items.length - 1).join(', ')} and ${items.last}',
};

class RelativeView extends StatelessWidget {
  const RelativeView({
    super.key,
    required this.controller,
    required this.peerSearch,
    this.intrinsicAdjusted = false,
  });

  /// The relative answer and the peer group being edited.
  final RelativeController controller;

  /// Search for adding peers - the same debounced, cached search as the main
  /// ticker field, kept for the screen so its cache outlives the sheet.
  final TickerSearchController peerSearch;

  /// True when the intrinsic valuation on screen has slider overrides. The
  /// relative comparison is always against the derived-assumption figure, and
  /// says so rather than letting the two be confused.
  final bool intrinsicAdjusted;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        if (controller.loading) return const _Loading();

        return switch (controller.outcome) {
          RelativeAvailable(:final report) => _Report(
            report: report,
            controller: controller,
            intrinsicAdjusted: intrinsicAdjusted,
            onAddPeer: () => showAddPeerSheet(
              context: context,
              controller: controller,
              search: peerSearch,
            ),
          ),
          RelativeNotApplicable(:final message, :final reasons) => _Panel(
            icon: Icons.block_rounded,
            title: 'No relative view',
            message: message,
            reasons: reasons,
          ),
          RelativeFailure(:final message) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _Panel(
                icon: Icons.cloud_off_rounded,
                title: 'The relative view could not be loaded',
                message: message,
              ),
              const SizedBox(height: AppSpacing.md),
              Center(
                child: OutlinedButton(
                  onPressed: controller.retry,
                  child: const Text('Try again'),
                ),
              ),
            ],
          ),
          null => const _Loading(),
        };
      },
    );
  }
}

class _Report extends StatelessWidget {
  const _Report({
    required this.report,
    required this.controller,
    required this.intrinsicAdjusted,
    required this.onAddPeer,
  });

  final RelativeReport report;
  final RelativeController controller;
  final bool intrinsicAdjusted;
  final VoidCallback onAddPeer;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final refreshing = controller.refreshing;

    return Column(
      key: const Key('relative-view'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const _Framing(),
        const SizedBox(height: AppGap.block),

        if (refreshing) ...[
          const LinearProgressIndicator(minHeight: 2),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'Updating with your peer group. Pricing new companies can take a '
            'little while.',
            key: const Key('relative-refreshing'),
            style: context.text.bodySmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
          const SizedBox(height: AppGap.block),
        ],
        if (controller.applyError != null) ...[
          Callout(
            tone: Tone.caution,
            text:
                'Your peer changes could not be applied: '
                '${controller.applyError} The figures below are still for the '
                'previous peer group.',
          ),
          const SizedBox(height: AppGap.block),
        ],
        if (controller.hasPendingChanges && !refreshing) ...[
          Text(
            'The figures below are for the current peer group until you '
            'apply your changes.',
            key: const Key('relative-stale'),
            style: context.text.labelSmall?.copyWith(color: colors.caution),
          ),
          const SizedBox(height: AppSpacing.md),
        ],

        // Dimmed while a new peer group is being priced: still readable, and
        // visibly not yet the answer for the group being asked about.
        AnimatedOpacity(
          opacity: refreshing ? 0.45 : 1,
          duration: const Duration(milliseconds: 150),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (report.hasFigure)
                _Figure(report: report)
              else
                _Panel(
                  key: const Key('relative-declined'),
                  icon: Icons.info_outline_rounded,
                  title: 'No relative figure for this company',
                  message: report.declineMessage,
                  reasons: report.declineReasons,
                ),

              const SizedBox(height: AppGap.block),
              _Comparison(report: report, intrinsicAdjusted: intrinsicAdjusted),

              if (report.note.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.md),
                Callout(key: const Key('relative-note'), text: report.note),
              ],
              if (report.warnings.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.md),
                NoteList(title: 'Keep in mind', notes: report.warnings),
              ],

              if (report.multiples.isNotEmpty) ...[
                const SizedBox(height: AppGap.section),
                SectionHeader(
                  title: 'Multiples',
                  subtitle: report.hasFigure
                      ? 'The company\'s own multiple beside the median of its peers.'
                      : 'Shown as information only. No value is drawn from them, and '
                            'like any market multiple they carry whatever the market '
                            'is getting wrong about the group.',
                ),
                const SizedBox(height: AppSpacing.md),
                AppCard(
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg,
                  ),
                  child: Column(
                    children: [
                      for (final (i, m) in report.multiples.indexed) ...[
                        if (i > 0) Divider(height: 1, color: colors.hairline),
                        _MultipleCard(
                          multiple: m,
                          ticker: report.ticker,
                          informationOnly: !report.hasFigure,
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),

        if (report.peerSelection != null) ...[
          const SizedBox(height: AppGap.section),
          _PeerGroup(
            selection: report.peerSelection!,
            controller: controller,
            onAddPeer: onAddPeer,
          ),
        ],
      ],
    );
  }
}

/// Before any number: what kind of number this is. Kept quiet, so the figure
/// below it is what the eye lands on.
class _Framing extends StatelessWidget {
  const _Framing();

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          Icons.compare_arrows_rounded,
          size: 18,
          color: colors.textSecondary,
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'RELATIVE · MARKET-BASED',
                style: context.text.labelMedium?.copyWith(
                  color: colors.textSecondary,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                'A second opinion: how the market prices similar companies '
                'right now. This is not the intrinsic value.',
                style: context.text.bodySmall?.copyWith(
                  color: colors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Figure extends StatelessWidget {
  const _Figure({required this.report});

  final RelativeReport report;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final applied = joinWithAnd(report.multiplesApplied);
    final vs = report.relativeVsPrice;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Eyebrow('RELATIVE VALUE PER SHARE'),
          const SizedBox(height: AppSpacing.md),
          // Neutral colour, and no up or down arrow: a market-based figure
          // is not a verdict on the price.
          Text(
            _money(report.relativeValue!),
            key: const Key('relative-figure'),
            style: context.text.displayLarge?.copyWith(
              fontWeight: FontWeight.w500,
            ),
          ),
          if (report.low != null && report.high != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              '${_money(report.low!)} to ${_money(report.high!)}'
              '${applied.isEmpty ? '' : ' across $applied'}',
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
          if (report.currentPrice != null) ...[
            const SizedBox(height: AppSpacing.lg),
            Divider(color: colors.hairline, height: 1),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Market price ${_money(report.currentPrice!)}'
              '${vs == null ? '' : ' · relative value ${(vs.abs() * 100).toStringAsFixed(0)}% '
                        '${vs < 0 ? 'below' : 'above'} it'}',
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// The two lenses side by side, always - including when there is no relative
/// figure, since the intrinsic value is still the answer.
class _Comparison extends StatelessWidget {
  const _Comparison({required this.report, required this.intrinsicAdjusted});

  final RelativeReport report;
  final bool intrinsicAdjusted;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final intrinsic = report.intrinsic;
    final method = intrinsic?.method == ValuationMethod.ddm ? 'DDM' : 'DCF';

    return AppCard(
      key: const Key('relative-comparison'),
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Expanded(
                  child: LabelledValue(
                    label: 'Intrinsic value ($method)',
                    value: intrinsic == null
                        ? '—'
                        : _money(intrinsic.valuePerShare),
                    valueKey: const Key('relative-intrinsic'),
                  ),
                ),
                VerticalDivider(width: AppSpacing.xxl, color: colors.hairline),
                Expanded(
                  child: LabelledValue(
                    label: 'Relative value',
                    value: report.hasFigure
                        ? _money(report.relativeValue!)
                        : 'Not produced',
                  ),
                ),
              ],
            ),
          ),
          if (report.comparisonStatement.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            Divider(height: 1, color: colors.hairline),
            const SizedBox(height: AppSpacing.md),
            Text(
              tidyBackendMessage(report.comparisonStatement),
              style: context.text.bodySmall?.copyWith(
                color: context.scheme.onSurface,
              ),
            ),
          ],
          if (intrinsicAdjusted) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              'The intrinsic value here uses the derived assumptions. Your '
              'slider changes apply only on the intrinsic tab.',
              style: context.text.labelSmall?.copyWith(color: colors.caution),
            ),
          ],
        ],
      ),
    );
  }
}

/// One multiple, as a row inside the shared multiples card.
class _MultipleCard extends StatelessWidget {
  const _MultipleCard({
    required this.multiple,
    required this.ticker,
    required this.informationOnly,
  });

  final RelativeMultiple multiple;
  final String ticker;
  final bool informationOnly;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final m = multiple;
    final premium = m.premiumToMedian;
    final secondary = context.text.labelSmall?.copyWith(
      color: colors.textSecondary,
    );

    return Padding(
      key: ValueKey('multiple-${m.name}'),
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(m.label, style: context.text.titleSmall)),
              if (!m.applicable) const Pill(label: 'Not applied'),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Expanded(
                child: LabelledValue(
                  label: ticker,
                  value: m.companyValue == null
                      ? '—'
                      : _multiple(m.companyValue!),
                ),
              ),
              Expanded(
                child: LabelledValue(
                  label: 'Peer median',
                  value: m.peerMedian == null ? '—' : _multiple(m.peerMedian!),
                ),
              ),
              Expanded(
                child: LabelledValue(
                  label: 'Implied',
                  value: m.impliedValuePerShare != null
                      ? _money(m.impliedValuePerShare!)
                      : (m.applicable && informationOnly ? 'Withheld' : '—'),
                ),
              ),
            ],
          ),
          if (premium != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              '$ticker trades ${(premium.abs() * 100).toStringAsFixed(0)}% '
              '${premium < 0 ? 'below' : 'above'} the peer median',
              style: context.text.bodySmall?.copyWith(
                color: context.scheme.onSurface,
              ),
            ),
          ],
          if (m.reason != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(tidyBackendMessage(m.reason!), style: secondary),
          ],
          if (m.companyReason != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(tidyBackendMessage(m.companyReason!), style: secondary),
          ],
          if (m.peers.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.md,
              runSpacing: 2,
              children: [
                for (final p in m.peers)
                  Text(
                    '${p.ticker} ${p.value == null ? '—' : _multiple(p.value!)}',
                    style: secondary,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// The peer group, and the place to change it.
///
/// Provenance is on every row - chosen automatically or added by you - and
/// every staged change is shown as staged until it is applied, so the list
/// never claims a group the figures were not computed from.
class _PeerGroup extends StatelessWidget {
  const _PeerGroup({
    required this.selection,
    required this.controller,
    required this.onAddPeer,
  });

  final PeerSelection selection;
  final RelativeController controller;
  final VoidCallback onAddPeer;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final rows = controller.rows;
    final excluded = controller.excluded;
    final busy = controller.refreshing;
    final edited = controller.isEdited || !selection.isAutomatic;

    return Column(
      key: const Key('relative-peers'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionHeader(
          title: 'Peer group',
          badge: Pill(
            key: const Key('peer-mode'),
            label: edited ? 'Edited by you' : 'Chosen automatically',
            tone: Tone.accent,
          ),
          trailing: controller.isEdited
              ? TextButton.icon(
                  key: const Key('peer-reset'),
                  onPressed: busy ? null : controller.resetOrDiscard,
                  icon: const Icon(Icons.restart_alt_rounded, size: 17),
                  label: const Text('Automatic'),
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                  ),
                )
              : null,
        ),
        const SizedBox(height: AppSpacing.md),

        AppCard(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.xs,
            AppSpacing.xs,
            AppSpacing.xs,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (rows.isEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    0,
                    AppSpacing.md,
                    AppSpacing.md,
                    AppSpacing.md,
                  ),
                  child: Text(
                    'No comparable companies were found. Add companies you '
                    'consider comparable to see how the market prices them.',
                    style: context.text.bodyMedium,
                  ),
                )
              else
                for (final (i, row) in rows.indexed) ...[
                  if (i > 0) Divider(height: 1, color: colors.hairline),
                  _PeerRowTile(row: row, controller: controller, busy: busy),
                ],
            ],
          ),
        ),

        const SizedBox(height: AppSpacing.md),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            key: const Key('peer-add'),
            onPressed: busy || controller.pending.added.length >= kMaxAddedPeers
                ? null
                : onAddPeer,
            icon: const Icon(Icons.add_rounded, size: 18),
            label: const Text('Add a peer'),
            // The theme's outlined style pads vertically only, for full-width
            // buttons; sized to its label, this one needs its own sides.
            style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.lg,
                vertical: AppSpacing.md,
              ),
            ),
          ),
        ),

        if (controller.hasPendingChanges) ...[
          const SizedBox(height: AppSpacing.md),
          _PendingBar(controller: controller),
        ],

        const SizedBox(height: AppSpacing.sm),
        // The fine print about the group, folded away so the rows and their
        // controls come first. Nothing is dropped: both open in place.
        if (selection.rule.isNotEmpty)
          _Disclosure(
            title: 'How these peers were chosen',
            children: [
              Text(
                edited
                    ? '${tidyBackendMessage(selection.rule)} Companies you add are '
                          'included even where automatic selection would leave '
                          'them out, with a warning when they differ.'
                    : tidyBackendMessage(selection.rule),
                style: context.text.bodySmall?.copyWith(
                  color: colors.textSecondary,
                ),
              ),
            ],
          ),
        if (excluded.isNotEmpty)
          _Disclosure(
            title: 'Considered and left out (${excluded.length})',
            children: [
              for (final e in excluded)
                Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      TickerChip(e.ticker),
                      const SizedBox(width: AppSpacing.md),
                      Expanded(
                        child: Text(
                          '${e.name == null ? '' : '${e.name}: '}${tidyBackendMessage(e.reason)}',
                          style: context.text.labelSmall?.copyWith(
                            color: colors.textSecondary,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
      ],
    );
  }
}

/// A quiet fold-out row for secondary detail.
class _Disclosure extends StatelessWidget {
  const _Disclosure({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return ExpansionTile(
      dense: true,
      visualDensity: VisualDensity.compact,
      expandedCrossAxisAlignment: CrossAxisAlignment.start,
      childrenPadding: const EdgeInsets.only(bottom: AppSpacing.sm),
      title: Text(
        title,
        style: context.text.bodySmall?.copyWith(
          color: context.scheme.onSurface,
          fontWeight: FontWeight.w600,
        ),
      ),
      children: children,
    );
  }
}

class _PeerRowTile extends StatelessWidget {
  const _PeerRowTile({
    required this.row,
    required this.controller,
    required this.busy,
  });

  final PeerRow row;
  final RelativeController controller;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final accent = context.scheme.primary;
    final struck =
        row.state == PeerRowState.toRemove || row.state == PeerRowState.removed;
    final staged = row.state != PeerRowState.current;

    final (String? status, Widget action) = switch (row.state) {
      PeerRowState.current => (
        null,
        IconButton(
          key: ValueKey('peer-remove-${row.ticker}'),
          tooltip: 'Remove ${row.ticker}',
          visualDensity: VisualDensity.compact,
          icon: Icon(
            Icons.close_rounded,
            size: 18,
            color: colors.textSecondary,
          ),
          onPressed: busy ? null : () => controller.removePeer(row.ticker),
        ),
      ),
      PeerRowState.toAdd => (
        'Will be added',
        IconButton(
          key: ValueKey('peer-remove-${row.ticker}'),
          tooltip: 'Don\'t add ${row.ticker}',
          visualDensity: VisualDensity.compact,
          icon: Icon(
            Icons.close_rounded,
            size: 18,
            color: colors.textSecondary,
          ),
          onPressed: busy ? null : () => controller.removePeer(row.ticker),
        ),
      ),
      PeerRowState.toRemove => (
        'Will be removed',
        TextButton(
          key: ValueKey('peer-undo-${row.ticker}'),
          onPressed: busy ? null : () => controller.undoRemoval(row.ticker),
          child: const Text('Undo'),
        ),
      ),
      PeerRowState.removed => (
        'Removed by you',
        TextButton(
          key: ValueKey('peer-restore-${row.ticker}'),
          onPressed: busy ? null : () => controller.restorePeer(row.ticker),
          child: const Text('Restore'),
        ),
      ),
      PeerRowState.toRestore => (
        'Will be restored',
        TextButton(
          key: ValueKey('peer-undo-${row.ticker}'),
          onPressed: busy ? null : () => controller.removePeer(row.ticker),
          child: const Text('Undo'),
        ),
      ),
    };

    return Container(
      key: ValueKey('peer-row-${row.ticker}'),
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      // A staged change is marked by a thin accent rule at the leading edge,
      // rather than a second border inside the card.
      decoration: staged
          ? BoxDecoration(
              border: Border(
                left: BorderSide(
                  color: accent.withValues(alpha: 0.7),
                  width: 2,
                ),
              ),
            )
          : null,
      child: Padding(
        padding: EdgeInsets.only(left: staged ? AppSpacing.sm : 0),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    row.name,
                    style: context.text.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w600,
                      decoration: struck ? TextDecoration.lineThrough : null,
                      color: struck ? colors.textSecondary : null,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    status == null
                        ? row.provenance
                        : '${row.provenance} · $status',
                    style: context.text.labelSmall?.copyWith(
                      color: staged ? accent : colors.textSecondary,
                      fontWeight: staged ? FontWeight.w600 : null,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            TickerChip(row.ticker),
            action,
          ],
        ),
      ),
    );
  }
}

/// Staged peer changes: one request when applied, none until then.
class _PendingBar extends StatelessWidget {
  const _PendingBar({required this.controller});

  final RelativeController controller;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final busy = controller.refreshing;
    return Container(
      key: const Key('peer-pending'),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.sm,
        AppSpacing.sm,
        AppSpacing.sm,
      ),
      decoration: BoxDecoration(
        color: colors.accentSurface,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Row(
        children: [
          Expanded(
            child: Text(
              'Peer group changed. The figures update when you apply.',
              style: context.text.bodySmall?.copyWith(
                color: context.scheme.onSurface,
              ),
            ),
          ),
          TextButton(
            key: const Key('peer-discard'),
            onPressed: busy ? null : controller.discard,
            child: const Text('Discard'),
          ),
          const SizedBox(width: AppSpacing.xs),
          FilledButton(
            key: const Key('peer-apply'),
            onPressed: busy ? null : controller.apply,
            child: const Text('Apply'),
          ),
        ],
      ),
    );
  }
}

/// Find and stage peers, using the same search as the main ticker field.
///
/// Stays open so several companies can be added before one apply, and shows
/// each staged addition so the user can see what they have picked.
Future<void> showAddPeerSheet({
  required BuildContext context,
  required RelativeController controller,
  required TickerSearchController search,
}) {
  search.dismiss();
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => _AddPeerSheet(controller: controller, search: search),
  ).whenComplete(search.dismiss);
}

class _AddPeerSheet extends StatefulWidget {
  const _AddPeerSheet({required this.controller, required this.search});

  final RelativeController controller;
  final TickerSearchController search;

  @override
  State<_AddPeerSheet> createState() => _AddPeerSheetState();
}

class _AddPeerSheetState extends State<_AddPeerSheet> {
  final _field = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  void _select(CompanySearchResult result) {
    final error = widget.controller.addPeer(result);
    setState(() => _error = error);
    if (error == null) {
      _field.clear();
      widget.search.dismiss();
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final controller = widget.controller;
    final search = widget.search;

    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.xl,
        0,
        AppSpacing.xl,
        MediaQuery.viewInsetsOf(context).bottom + AppSpacing.xl,
      ),
      child: ListenableBuilder(
        listenable: Listenable.merge([controller, search]),
        builder: (context, _) {
          final staged = controller.rows
              .where((r) => r.state == PeerRowState.toAdd)
              .toList();
          return Column(
            key: const Key('add-peer-sheet'),
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Add a peer to ${controller.ticker}',
                style: context.text.titleMedium,
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                'Choose companies you consider comparable. Nothing is fetched '
                'until you apply.',
                style: context.text.bodySmall?.copyWith(
                  color: colors.textSecondary,
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              TextField(
                key: const Key('add-peer-field'),
                controller: _field,
                autofocus: true,
                autocorrect: false,
                textCapitalization: TextCapitalization.characters,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(
                    RegExp(r"[A-Za-z0-9.\-&', ]"),
                  ),
                  LengthLimitingTextInputFormatter(50),
                ],
                decoration: InputDecoration(
                  hintText: 'Ticker or company name',
                  prefixIcon: Icon(
                    Icons.search_rounded,
                    size: 20,
                    color: colors.textSecondary,
                  ),
                  errorText: _error,
                  errorMaxLines: 3,
                ),
                onChanged: (text) {
                  if (_error != null) setState(() => _error = null);
                  search.onQueryChanged(text);
                },
              ),
              if (search.isOpen) ...[
                const SizedBox(height: AppSpacing.sm),
                SearchDropdown(
                  key: const Key('add-peer-dropdown'),
                  controller: search,
                  onSelected: _select,
                  maxHeight: 260,
                  elevation: 2,
                  unavailableText:
                      'Search is unavailable right now. Try again in a moment.',
                  emptyText: 'No matching US-listed companies.',
                ),
              ],
              if (staged.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.lg),
                Text('To be added', style: context.text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  runSpacing: AppSpacing.sm,
                  children: [
                    for (final r in staged)
                      InputChip(
                        key: ValueKey('staged-${r.ticker}'),
                        label: Text('${r.ticker} · ${r.name}'),
                        onDeleted: () => controller.removePeer(r.ticker),
                      ),
                  ],
                ),
              ],
              const SizedBox(height: AppSpacing.lg),
              FilledButton(
                key: const Key('add-peer-done'),
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Done'),
              ),
            ],
          );
        },
      ),
    );
  }
}

/// A considered "no" - no relative view, or no figure - with its reasons.
class _Panel extends StatelessWidget {
  const _Panel({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.reasons = const [],
  });

  final IconData icon;
  final String title;
  final String message;
  final List<String> reasons;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 20, color: colors.textSecondary),
              const SizedBox(width: AppSpacing.md),
              Expanded(child: Text(title, style: context.text.titleSmall)),
            ],
          ),
          if (message.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            Text(tidyBackendMessage(message), style: context.text.bodyMedium),
          ],
          for (final reason in reasons) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              tidyBackendMessage(reason),
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxxl),
      child: Column(
        children: [
          const SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(strokeWidth: 2.5),
          ),
          const SizedBox(height: AppSpacing.xl),
          Text('Finding comparable companies', style: context.text.titleMedium),
          const SizedBox(height: AppSpacing.xs),
          Text(
            'Pricing several companies can take a little while.',
            style: context.text.bodySmall,
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}
