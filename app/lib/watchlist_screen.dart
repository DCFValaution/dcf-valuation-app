/// The watchlist: what you are following, and what it is worth.
///
/// TWO SOURCES, ON PURPOSE
/// -----------------------
/// The rows paint from Firestore — the reader's own copy of what the
/// screen said last time — and then a background request to the API
/// replaces them with today's figures. That is not an optimisation, it is
/// the difference between a usable screen and an unusable one: waking the
/// free Render instance took 19.4 seconds when measured, and a watchlist
/// that waited on it would be a spinner.
///
/// So the screen is never empty, every figure carries its own date, and a
/// refresh that fails leaves yesterday's answer visible rather than a
/// blank. "Showing what you last saw" is a true sentence; a spinner that
/// never resolves is not.
library;

import 'package:flutter/material.dart';

import 'formatting.dart';
import 'loading_states.dart';
import 'theme.dart';
import 'user_data_store.dart';
import 'valuation_api.dart';

/// Sign before the symbol, not inside the number: a negative figure per
/// share reads "-$1.50", never "$-1.50". Matches the market-snapshot
/// panel, which is the other place a per-share figure is printed.
String _perShare(double value) {
  final symbol = currencySymbol('USD');
  return '${value < 0 ? '-' : ''}$symbol${fixed(value.abs(), 2)}';
}

/// Day and month, because a watchlist row is about recency rather than
/// about a date in a filing. The year appears only once it matters.
String _asOf(DateTime when) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  final now = DateTime.now();
  final stamp = '${when.day} ${months[when.month - 1]}';
  return when.year == now.year ? stamp : '$stamp ${when.year}';
}

class WatchlistScreen extends StatefulWidget {
  const WatchlistScreen({
    super.key,
    required this.store,
    required this.api,
    this.onOpenCompany,
  });

  final UserDataStore store;
  final ValuationApi api;

  /// Tapping a row opens the full valuation.
  final void Function(String ticker)? onOpenCompany;

  @override
  State<WatchlistScreen> createState() => _WatchlistScreenState();
}

class _WatchlistScreenState extends State<WatchlistScreen> {
  bool _refreshing = false;
  String? _refreshError;
  List<String> _lastRefreshed = const [];

  @override
  Widget build(BuildContext context) {
    // Not a bare StreamBuilder: the hand-written version returned a
    // spinner whenever `data` was null, which includes the stream having
    // FAILED. With the Firestore rules undeployed every read was denied
    // and this screen span forever with the reason in an error field
    // nobody read.
    return LoadingStream<List<WatchlistEntry>>(
      stream: widget.store.watchlist,
      what: 'your watchlist',
      builder: (context, rows) {
        if (rows.isEmpty) return _empty(context);
        _maybeRefresh(rows);
        return _list(context, rows);
      },
    );
  }

  /// Ask the API for today's figures, once per set of tickers.
  ///
  /// Guarded against the stream rebuilding: `cacheRows` writes back to the
  /// same document this screen is listening to, so an unguarded refresh
  /// would trigger itself forever.
  void _maybeRefresh(List<WatchlistEntry> rows) {
    final tickers = rows.map((r) => r.ticker).toList();
    if (_refreshing || _sameAs(tickers, _lastRefreshed)) return;
    _lastRefreshed = tickers;
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh(tickers));
  }

  static bool _sameAs(List<String> a, List<String> b) =>
      a.length == b.length && List.generate(a.length, (i) => a[i] == b[i])
          .every((same) => same);

  Future<void> _refresh(List<String> tickers) async {
    if (tickers.isEmpty) return;
    setState(() {
      _refreshing = true;
      _refreshError = null;
    });
    try {
      final fresh = await widget.api.watchlist(tickers);
      await widget.store.cacheRows(fresh);
      if (mounted) setState(() => _refreshing = false);
    } catch (e) {
      // The rows on screen are still real figures with real dates. Saying
      // the refresh failed is enough; blanking them would be worse than
      // the staleness.
      if (mounted) {
        setState(() {
          _refreshing = false;
          _refreshError = 'Could not refresh just now.';
        });
      }
    }
  }

  Widget _empty(BuildContext context) {
    final colors = context.colors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.bookmark_border_rounded, size: 40, color: colors.neutral),
            const SizedBox(height: AppSpacing.lg),
            Text('Nothing on your watchlist yet',
                style: context.text.titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Value a company, then add it from the valuation screen.',
              textAlign: TextAlign.center,
              style: context.text.bodySmall
                  ?.copyWith(color: colors.textSecondary),
            ),
          ],
        ),
      ),
    );
  }

  Widget _list(BuildContext context, List<WatchlistEntry> rows) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _status(context, rows),
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.fromLTRB(
                AppSpacing.xl, 0, AppSpacing.xl, AppSpacing.xxl),
            itemCount: rows.length,
            separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
            itemBuilder: (context, i) => _Row(
              key: Key('watchlist-row-${rows[i].ticker}'),
              entry: rows[i],
              onTap: () => widget.onOpenCompany?.call(rows[i].ticker),
              onRemove: () => widget.store.removeTicker(rows[i].ticker),
            ),
          ),
        ),
      ],
    );
  }

  Widget _status(BuildContext context, List<WatchlistEntry> rows) {
    final colors = context.colors;
    final newest = rows
        .map((r) => r.asOf)
        .whereType<DateTime>()
        .fold<DateTime?>(null, (a, b) => a == null || b.isAfter(a) ? b : a);

    final String line;
    if (_refreshing) {
      line = 'Refreshing…';
    } else if (_refreshError != null) {
      line = newest == null
          ? '$_refreshError'
          : '$_refreshError Showing what you last saw.';
    } else if (newest == null) {
      line = 'Not valued yet';
    } else {
      line = 'Updated ${_asOf(newest)}';
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.xl, AppSpacing.md, AppSpacing.xl, AppSpacing.lg),
      child: Text(
        line,
        key: const Key('watchlist-status'),
        style: context.text.labelSmall?.copyWith(
          color: _refreshError != null ? colors.caution : colors.textSecondary,
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({super.key, required this.entry, this.onTap, this.onRemove});

  final WatchlistEntry entry;
  final VoidCallback? onTap;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final upside = entry.upside;

    return Dismissible(
      key: Key('dismiss-${entry.ticker}'),
      direction: DismissDirection.endToStart,
      onDismissed: (_) => onRemove?.call(),
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: AppSpacing.xl),
        decoration: BoxDecoration(
          color: colors.negativeSurface,
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
        child: Icon(Icons.delete_outline_rounded, color: colors.negative),
      ),
      child: Material(
        color: colors.accentSurface.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(AppRadius.control),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AppRadius.control),
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(entry.ticker,
                              style: context.text.titleSmall
                                  ?.copyWith(fontWeight: FontWeight.w700)),
                          if (entry.name != null) ...[
                            const SizedBox(height: 2),
                            Text(entry.name!,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: context.text.labelSmall
                                    ?.copyWith(color: colors.textSecondary)),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(width: AppSpacing.md),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(
                          entry.refused
                              ? 'No figure'
                              : entry.lastValue == null
                                  ? '—'
                                  : _perShare(entry.lastValue!),
                          style: context.text.titleSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                            color: entry.refused ? colors.textSecondary : null,
                          ),
                        ),
                        if (entry.lastPrice != null) ...[
                          const SizedBox(height: 2),
                          Text(_perShare(entry.lastPrice!),
                              style: context.text.labelSmall
                                  ?.copyWith(color: colors.textSecondary)),
                        ],
                      ],
                    ),
                    const SizedBox(width: AppSpacing.md),
                    _Chip(entry: entry, upside: upside),
                  ],
                ),
                if (entry.refused) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Text(
                    'A standard valuation isn’t the right tool here.',
                    style: context.text.labelSmall
                        ?.copyWith(color: colors.textSecondary),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.entry, required this.upside});

  final WatchlistEntry entry;
  final double? upside;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    late final String label;
    late final Color fg;
    late final Color bg;

    if (entry.refused) {
      label = 'Refused';
      fg = colors.textSecondary;
      bg = colors.accentSurface;
    } else if (upside == null) {
      label = '—';
      fg = colors.textSecondary;
      bg = colors.accentSurface;
    } else if (upside! >= 0) {
      label = '+${(upside! * 100).toStringAsFixed(1)}%';
      fg = colors.positive;
      bg = colors.positiveSurface;
    } else {
      label = '${(upside! * 100).toStringAsFixed(1)}%';
      fg = colors.negative;
      bg = colors.negativeSurface;
    }

    return Container(
      constraints: const BoxConstraints(minWidth: 62),
      alignment: Alignment.center,
      padding:
          const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: 4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      child: Text(label,
          style: context.text.labelSmall
              ?.copyWith(color: fg, fontWeight: FontWeight.w700)),
    );
  }
}
