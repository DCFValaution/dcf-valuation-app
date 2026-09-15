/// The relative view's state: what the backend last said, the peer group the
/// user is editing, and when to ask again.
///
/// Peer edits are staged and applied on confirm rather than sent as they are
/// made. Each request prices every peer through a throttled data source - the
/// first edited request for AAPL took sixteen seconds - so adding three
/// companies must cost one request, not three. Answers are cached by peer set,
/// so undoing an edit, or resetting to the automatic group, costs nothing.
///
/// Kept apart from the widgets so the rules can be tested directly.
library;

import 'package:flutter/foundation.dart';

import 'valuation_api.dart';

/// The backend's limit on peers a user may add.
const int kMaxAddedPeers = 8;

/// A peer group as edits to the automatic selection: companies added, and
/// automatically chosen companies removed.
@immutable
class PeerEdits {
  const PeerEdits({
    this.added = const [],
    this.removed = const {},
    this.names = const {},
  });

  static const empty = PeerEdits();

  /// In the order the user added them.
  final List<String> added;
  final Set<String> removed;

  /// Names for added companies, known from search before the backend has
  /// confirmed them.
  final Map<String, String> names;

  bool get isEmpty => added.isEmpty && removed.isEmpty;

  /// Identity of the peer set - order-independent, so the same group reached
  /// by a different route hits the same cache entry.
  String get key {
    final a = [...added]..sort();
    final r = [...removed]..sort();
    return 'add:${a.join(',')}|remove:${r.join(',')}';
  }

  PeerEdits withAdded(String ticker, String name) => PeerEdits(
    added: [...added, ticker],
    removed: removed,
    names: {...names, ticker: name},
  );

  PeerEdits withoutAdded(String ticker) => PeerEdits(
    added: added.where((t) => t != ticker).toList(),
    removed: removed,
    names: names,
  );

  PeerEdits withRemoved(String ticker) =>
      PeerEdits(added: added, removed: {...removed, ticker}, names: names);

  PeerEdits withoutRemoved(String ticker) => PeerEdits(
    added: added,
    removed: removed.where((t) => t != ticker).toSet(),
    names: names,
  );
}

/// How a peer row should read while the group is being edited.
enum PeerRowState {
  /// In the group the figure was computed from, and staying.
  current,

  /// Staged to be added; not yet in the figure.
  toAdd,

  /// In the figure, staged to be removed.
  toRemove,

  /// Removed by the user from the automatic group, and applied.
  removed,

  /// Removed and applied, now staged to come back.
  toRestore,
}

@immutable
class PeerRow {
  const PeerRow({
    required this.ticker,
    required this.name,
    required this.provenance,
    required this.state,
  });

  final String ticker;
  final String name;

  /// "Chosen automatically" or "Added by you".
  final String provenance;
  final PeerRowState state;

  bool get isAutomatic => provenance == 'Chosen automatically';
}

class RelativeController extends ChangeNotifier {
  RelativeController({required this.api, required this.ticker});

  final ValuationApi api;
  final String ticker;

  RelativeOutcome? _outcome;
  RelativeOutcome? get outcome => _outcome;

  /// True until the first answer arrives: there is nothing to show yet.
  bool get loading => _outcome == null && _busy;

  /// True while an edited peer group is being fetched, with the previous
  /// answer still on screen.
  bool get refreshing => _outcome != null && _busy;

  bool _busy = false;
  int _seq = 0;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  /// Set when applying edits failed. The previous figure stays, still
  /// matching the peers it was computed from, and the edits stay staged.
  String? _applyError;
  String? get applyError => _applyError;

  PeerEdits _applied = PeerEdits.empty;
  PeerEdits _pending = PeerEdits.empty;
  PeerEdits get applied => _applied;
  PeerEdits get pending => _pending;

  bool get hasPendingChanges => _pending.key != _applied.key;
  bool get isEdited => !_applied.isEmpty || !_pending.isEmpty;

  final Map<String, RelativeOutcome> _cache = {};

  /// Every request actually sent, for tests and for anyone checking this
  /// does not hammer the server.
  int _requests = 0;
  int get requestCount => _requests;

  /// Names seen for each ticker, so a removed peer keeps its name after the
  /// backend stops listing it.
  final Map<String, String> _names = {};

  RelativeReport? get report => switch (_outcome) {
    RelativeAvailable(:final report) => report,
    _ => null,
  };

  /// Open the view: fetch the automatic group the first time only.
  void open() {
    if (_outcome == null && !_busy) _fetch(_applied);
  }

  void retry() => _fetch(_applied);

  /// Stage [company] as a peer. Returns why it cannot be added, or null.
  String? addPeer(CompanySearchResult company) {
    final t = company.ticker.trim().toUpperCase();
    if (t.isEmpty) return 'Choose a company to add.';
    if (t == ticker.toUpperCase()) {
      return 'That is the company being valued, so it cannot be its own peer.';
    }
    // Removed from the automatic group: adding it back means restoring it.
    if (_pending.removed.contains(t)) {
      _setPending(_pending.withoutRemoved(t));
      return null;
    }
    final inGroup =
        report?.peerSelection?.peers.any((p) => p.ticker == t) ?? false;
    final droppedAdded =
        _applied.added.contains(t) && !_pending.added.contains(t);
    if (droppedAdded) {
      _setPending(_pending.withAdded(t, company.name));
      return null;
    }
    if (inGroup || _pending.added.contains(t)) {
      return '$t is already in the peer group.';
    }
    if (_pending.added.length >= kMaxAddedPeers) {
      return 'At most $kMaxAddedPeers peers can be added.';
    }
    _setPending(_pending.withAdded(t, company.name));
    return null;
  }

  /// Stage removing [peerTicker] - an automatic peer or one the user added.
  void removePeer(String peerTicker) {
    final t = peerTicker.toUpperCase();
    if (_pending.added.contains(t)) {
      _setPending(_pending.withoutAdded(t));
    } else {
      _setPending(_pending.withRemoved(t));
    }
  }

  /// Undo a staged or applied removal of an automatic peer.
  void restorePeer(String peerTicker) =>
      _setPending(_pending.withoutRemoved(peerTicker.toUpperCase()));

  /// Undo a staged removal, whichever kind of peer it was.
  void undoRemoval(String peerTicker) {
    final t = peerTicker.toUpperCase();
    if (_pending.removed.contains(t)) {
      _setPending(_pending.withoutRemoved(t));
    } else if (_applied.added.contains(t) && !_pending.added.contains(t)) {
      _setPending(_pending.withAdded(t, _applied.names[t] ?? _names[t] ?? t));
    }
  }

  /// Leave the group as the automatic selection: discard staged edits if
  /// nothing has been applied, otherwise fetch the automatic group back.
  Future<void> resetOrDiscard() async {
    if (_applied.isEmpty) {
      discard();
    } else {
      await resetToAutomatic();
    }
  }

  void discard() {
    _applyError = null;
    _setPending(_applied);
  }

  /// Fetch the relative view for the staged peer group.
  Future<void> apply() async {
    if (!hasPendingChanges || _busy) return;
    await _fetch(_pending);
  }

  /// Back to the automatic group - usually straight from the cache.
  Future<void> resetToAutomatic() async {
    _pending = PeerEdits.empty;
    _applyError = null;
    await _fetch(PeerEdits.empty);
  }

  void _setPending(PeerEdits edits) {
    _pending = edits;
    notifyListeners();
  }

  Future<void> _fetch(PeerEdits edits) async {
    final previousApplied = _applied;
    final previousOutcome = _outcome;
    _applyError = null;

    final cached = _cache[edits.key];
    if (cached != null) {
      _seq++;
      _applied = edits;
      _pending = edits;
      _outcome = cached;
      _busy = false;
      _learnNames(cached);
      notifyListeners();
      return;
    }

    final seq = ++_seq;
    _busy = true;
    notifyListeners();

    _requests++;
    final result = edits.isEmpty
        ? await api.relative(ticker)
        : await api.relative(
            ticker,
            addPeers: edits.added,
            removePeers: edits.removed.toList(),
          );

    // Superseded, or the company was left while this was in flight.
    if (_disposed || seq != _seq) return;
    _busy = false;

    if (result is RelativeFailure && previousOutcome is RelativeAvailable) {
      // Keep the figure the user can still trust - it matches the peers it
      // was computed from - and leave the edits staged to try again.
      _applied = previousApplied;
      _outcome = previousOutcome;
      _applyError = result.message;
      notifyListeners();
      return;
    }

    // Only answers are cached; a throttle is not one.
    if (result is! RelativeFailure) _cache[edits.key] = result;
    _applied = edits;
    _pending = edits;
    _outcome = result;
    _learnNames(result);
    notifyListeners();
  }

  void _learnNames(RelativeOutcome outcome) {
    if (outcome is! RelativeAvailable) return;
    for (final p
        in outcome.report.peerSelection?.peers ?? const <RelativePeer>[]) {
      if (p.name.isNotEmpty) _names[p.ticker] = p.name;
    }
  }

  /// The peer list as it should read now: the group behind the figure, with
  /// staged additions and removals marked, and applied removals kept visible
  /// so they can be undone.
  List<PeerRow> get rows {
    final rows = <PeerRow>[];
    final peers = report?.peerSelection?.peers ?? const <RelativePeer>[];
    final inFigure = peers.map((p) => p.ticker).toSet();

    for (final p in peers) {
      final stagedOut =
          _pending.removed.contains(p.ticker) ||
          (_applied.added.contains(p.ticker) &&
              !_pending.added.contains(p.ticker));
      rows.add(
        PeerRow(
          ticker: p.ticker,
          name: p.name,
          provenance: p.provenanceLabel,
          state: stagedOut ? PeerRowState.toRemove : PeerRowState.current,
        ),
      );
    }
    for (final t in _pending.added) {
      if (inFigure.contains(t)) continue;
      rows.add(
        PeerRow(
          ticker: t,
          name: _pending.names[t] ?? _names[t] ?? t,
          provenance: 'Added by you',
          state: PeerRowState.toAdd,
        ),
      );
    }
    for (final t in {..._applied.removed, ..._pending.removed}) {
      if (inFigure.contains(t)) continue;
      rows.add(
        PeerRow(
          ticker: t,
          name: _names[t] ?? t,
          provenance: 'Chosen automatically',
          state: _pending.removed.contains(t)
              ? PeerRowState.removed
              : PeerRowState.toRestore,
        ),
      );
    }
    return rows;
  }

  /// Companies the backend left out, minus any now in the group.
  ///
  /// The automatic selection's exclusions are reported even for a company the
  /// user has since added - AAPL's list says MSFT was left out for being in
  /// another industry while MSFT sits in the group. Showing both would
  /// contradict itself; the industry difference is already in the backend's
  /// warnings for a peer the user chose. Removals are shown in the peer list
  /// instead, where they can be undone.
  List<ExcludedPeer> get excluded {
    final selection = report?.peerSelection;
    if (selection == null) return const [];
    final inGroup = selection.peers.map((p) => p.ticker).toSet();
    return selection.excluded
        .where(
          (e) => !inGroup.contains(e.ticker) && e.reason != 'removed by you',
        )
        .toList();
  }
}
