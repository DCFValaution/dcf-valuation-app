/// Waiting, and the two ways it can end.
///
/// WHY THIS IS ONE WIDGET AND NOT FOUR
/// -----------------------------------
/// Every screen behind an account had the same shape:
///
///     if (snapshot.data == null) return CircularProgressIndicator();
///
/// which spins forever when the stream errors. Firestore's rules were
/// never deployed, so every read was denied, and every one of those
/// screens showed a spinner that could never stop — with the real reason
/// (`PERMISSION_DENIED`) sitting in an error field nobody looked at.
///
/// A spinner that cannot stop is the worst state a screen has: it is
/// indistinguishable from slowness, so the reader has no way to know
/// whether to wait or to act. Four screens made the same mistake, so the
/// fix is one widget rather than four corrections.
library;

import 'package:flutter/material.dart';

import 'release_log.dart';
import 'theme.dart';

// Re-exported so the screens that show a failure and the screens that
// only log one import the same thing.
export 'release_log.dart' show logFailure, logStep;

/// What went wrong, in words, plus a way to try again.
class FailedToLoad extends StatelessWidget {
  const FailedToLoad({
    super.key,
    required this.what,
    required this.error,
    this.onRetry,
  });

  /// What was being loaded, named: "your watchlist", "your saved
  /// valuations". A screen that says only "Something went wrong" leaves
  /// the reader guessing which part of it is broken.
  final String what;

  final Object error;
  final VoidCallback? onRetry;

  /// Permission denied has a cause a developer can act on and a reader
  /// cannot, so it says something true for both without the jargon.
  bool get _denied {
    final text = '$error'.toLowerCase();
    return text.contains('permission-denied') ||
        text.contains('permission_denied') ||
        text.contains('insufficient permissions');
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.cloud_off_rounded, size: 36, color: colors.caution),
            const SizedBox(height: AppSpacing.lg),
            Text(
              'Could not load $what',
              key: const Key('failed-to-load'),
              textAlign: TextAlign.center,
              style: context.text.titleSmall
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              _denied
                  ? 'The server refused the request. Nothing is lost — your '
                      'saved work is still there.'
                  : 'Check your connection and try again. Nothing is lost.',
              textAlign: TextAlign.center,
              style: context.text.bodySmall
                  ?.copyWith(color: colors.textSecondary, height: 1.45),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: AppSpacing.xl),
              FilledButton.icon(
                key: const Key('failed-to-load-retry'),
                onPressed: onRetry,
                icon: const Icon(Icons.refresh_rounded, size: 18),
                label: const Text('Try again'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// A [StreamBuilder] that cannot spin forever.
///
/// Three outcomes, all of them reachable: waiting, failed, or loaded.
/// The second is the one the hand-written versions kept forgetting.
class LoadingStream<T> extends StatefulWidget {
  const LoadingStream({
    super.key,
    required this.stream,
    required this.what,
    required this.builder,
  });

  /// Built fresh on each retry, so "try again" really does re-subscribe
  /// rather than re-render the same dead stream.
  final Stream<T> Function() stream;

  final String what;
  final Widget Function(BuildContext, T) builder;

  @override
  State<LoadingStream<T>> createState() => _LoadingStreamState<T>();
}

class _LoadingStreamState<T> extends State<LoadingStream<T>> {
  late Stream<T> _stream = widget.stream();
  int _attempt = 0;

  void _retry() => setState(() {
        _attempt++;
        _stream = widget.stream();
      });

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<T>(
      key: ValueKey(_attempt),
      stream: _stream,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          logFailure(widget.what, snapshot.error!, snapshot.stackTrace);
          return FailedToLoad(
            what: widget.what,
            error: snapshot.error!,
            onRetry: _retry,
          );
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        return widget.builder(context, snapshot.data as T);
      },
    );
  }
}
