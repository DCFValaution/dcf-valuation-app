/// The sensitivity table: how far the per-share figure moves when the two
/// assumptions it rests on most heavily are changed.
///
/// Every honesty note in the app ends by pointing here, so this is what turns
/// "the figure is an output of assumptions" from a sentence into something
/// the reader can see. Two choices follow from that:
///
///   * no green-to-red shading - a heatmap of values reads as a map of good
///     and bad outcomes, and invites picking the cell you like;
///   * the centre cell is outlined and printed by the same formatter as the
///     headline, so anyone checking can see it is the figure above.
library;

import 'package:flutter/material.dart';

import 'theme.dart';
import 'valuation_api.dart';

class SensitivityTable extends StatelessWidget {
  const SensitivityTable({
    super.key,
    required this.grid,
    required this.formatValue,
    required this.title,
    required this.explanation,
    this.stale = false,
  });

  final SensitivityGrid grid;

  /// The headline's own formatter, so the centre cell matches it exactly.
  final String Function(double) formatValue;

  final String title;
  final String explanation;

  /// True while the figure above reflects assumptions this table does not -
  /// mid-drag, or while a change is being confirmed. The table stays visible
  /// but says it is behind, rather than sitting beside a headline it no
  /// longer describes.
  final bool stale;

  static String _axis(double v, bool percent) =>
      percent ? '${(v * 100).toStringAsFixed(1)}%' : '${v.round()}y';

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final accent = context.scheme.primary;
    final values = grid.values.toList();
    final low = values.reduce((a, b) => a < b ? a : b);
    final high = values.reduce((a, b) => a > b ? a : b);
    final flipsSign = low < 0 && high > 0;

    final labelStyle = context.text.labelSmall?.copyWith(
      color: colors.textSecondary,
      fontWeight: FontWeight.w600,
    );
    final cellStyle = context.text.bodySmall?.copyWith(
      fontFeatures: const [FontFeature.tabularFigures()],
    );

    TableRow headerRow() => TableRow(
      children: [
        const SizedBox.shrink(),
        for (var c = 0; c < grid.columnValues.length; c++)
          _Cell(
            text: _axis(grid.columnValues[c], grid.columnsArePercent),
            style: labelStyle?.copyWith(
              color: c == grid.centreCol ? accent : null,
            ),
          ),
      ],
    );

    TableRow valueRow(int r) => TableRow(
      children: [
        _Cell(
          text: _axis(grid.rowValues[r], grid.rowsArePercent),
          style: labelStyle?.copyWith(
            color: r == grid.centreRow ? accent : null,
          ),
          alignEnd: false,
        ),
        for (var c = 0; c < grid.columnValues.length; c++)
          _ValueCell(
            value: grid.cells[r][c],
            format: formatValue,
            style: cellStyle,
            centre: r == grid.centreRow && c == grid.centreCol,
          ),
      ],
    );

    return Container(
      key: const Key('sensitivity-table'),
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: context.scheme.surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: context.text.titleSmall),
          const SizedBox(height: AppSpacing.xs),
          Text(
            explanation,
            style: context.text.bodySmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
          const SizedBox(height: AppSpacing.lg),

          // Column axis name over the value columns, row axis name beside the
          // first column, so both are read before the numbers.
          Text('${grid.columnAxis} →', style: labelStyle),
          const SizedBox(height: AppSpacing.xs),
          AnimatedOpacity(
            opacity: stale ? 0.4 : 1,
            duration: const Duration(milliseconds: 150),
            // Scales down rather than overflowing: a five-by-five grid of
            // dollar figures is near the limit of a phone's width, and a
            // large value must shrink the table, never push it off screen.
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Table(
                defaultColumnWidth: const IntrinsicColumnWidth(),
                defaultVerticalAlignment: TableCellVerticalAlignment.middle,
                children: [
                  headerRow(),
                  for (var r = 0; r < grid.rowValues.length; r++) valueRow(r),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text('↓ ${grid.rowAxis}', style: labelStyle),

          const SizedBox(height: AppSpacing.md),
          if (stale)
            Text(
              'Out of date · updates when the figure above is recalculated',
              style: context.text.labelSmall?.copyWith(color: colors.caution),
            )
          else ...[
            // Concrete, not abstract: the actual spread in this table.
            Text(
              'Across this table the figure runs from ${formatValue(low)} to '
              '${formatValue(high)}'
              '${flipsSign ? ', changing sign along the way' : ''}. '
              'The outlined cell is the figure above.',
              key: const Key('sensitivity-range'),
              style: context.text.bodySmall,
            ),
            if (grid.hasBlanks) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                '— ${grid.blankMeaning}.',
                style: context.text.labelSmall?.copyWith(
                  color: colors.textSecondary,
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

class _Cell extends StatelessWidget {
  const _Cell({required this.text, this.style, this.alignEnd = true});

  final String text;
  final TextStyle? style;
  final bool alignEnd;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 7),
    child: Text(
      text,
      style: style,
      textAlign: alignEnd ? TextAlign.end : TextAlign.start,
    ),
  );
}

class _ValueCell extends StatelessWidget {
  const _ValueCell({
    required this.value,
    required this.format,
    required this.style,
    required this.centre,
  });

  final double? value;
  final String Function(double) format;
  final TextStyle? style;
  final bool centre;

  @override
  Widget build(BuildContext context) {
    final accent = context.scheme.primary;
    final v = value;
    final text = v == null ? '—' : format(v);

    return Container(
      key: centre ? const Key('sensitivity-centre') : null,
      margin: const EdgeInsets.all(1.5),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
      decoration: centre
          ? BoxDecoration(
              color: context.colors.accentSurface,
              border: Border.all(color: accent, width: 1.6),
              borderRadius: BorderRadius.circular(6),
            )
          : null,
      child: Text(
        text,
        textAlign: TextAlign.end,
        style: style?.copyWith(
          fontWeight: centre ? FontWeight.w800 : null,
          color: v == null ? context.colors.textSecondary : null,
        ),
      ),
    );
  }
}
