/// Search-as-you-type for the ticker field, without spamming the backend.
///
/// Three things keep a typed company name from turning into a request per
/// keystroke, each protecting something different:
///
///   * a debounce, so only a pause in typing asks the server anything -
///     typing "bank of am" costs one request, not ten;
///   * a cache of recent answers, so backspacing over a query, or retyping
///     one, costs nothing and appears instantly;
///   * a cooldown after a 429, so a throttled server is not asked again the
///     moment the next character arrives.
///
/// Kept apart from the widget so the timing can be tested on a fake clock.
library;

import 'dart:async';
import 'dart:collection';

import 'package:flutter/foundation.dart';

import 'valuation_api.dart';

/// What the dropdown should show right now.
enum SearchPhase {
  /// Nothing to show: an empty field, or the dropdown was dismissed.
  idle,

  /// A request is in flight. [TickerSearchController.results] may still hold
  /// the previous answer, which stays visible rather than flickering away.
  loading,

  /// The server answered; [TickerSearchController.results] may be empty.
  results,

  /// The search could not be run. Typing a ticker still works.
  unavailable,
}

typedef SearchFetcher = Future<SearchOutcome> Function(String query);

class TickerSearchController extends ChangeNotifier {
  TickerSearchController({
    required this.fetch,
    this.debounce = const Duration(milliseconds: 300),
    this.cooldown = const Duration(seconds: 10),
    this.cacheSize = 50,
    DateTime Function()? clock,
  }) : _now = clock ?? DateTime.now;

  /// Runs one search against the server.
  final SearchFetcher fetch;

  /// How long typing must pause before a request is sent. The same pattern as
  /// the slider confirmation, for the same reason.
  final Duration debounce;

  /// How long to stop asking after being told to slow down.
  final Duration cooldown;

  /// Recent answers kept. Small: this is for backspacing and retyping, not an
  /// offline index.
  final int cacheSize;

  final DateTime Function() _now;

  SearchPhase _phase = SearchPhase.idle;
  SearchPhase get phase => _phase;

  List<CompanySearchResult> _results = const [];
  List<CompanySearchResult> get results => _results;

  /// The query the current [results] answer.
  String _resultsQuery = '';
  String get resultsQuery => _resultsQuery;

  /// Every request made, for tests and for anyone checking this does not
  /// spam the server.
  int _requestCount = 0;
  int get requestCount => _requestCount;

  final LinkedHashMap<String, List<CompanySearchResult>> _cache =
      LinkedHashMap();

  Timer? _timer;
  int _seq = 0;
  String _pending = '';
  DateTime? _cooldownUntil;

  /// Set by [dismiss] so the text a selection just wrote into the field is not
  /// immediately searched for again, reopening the dropdown it closed.
  String? _dismissedFor;

  /// Whether the dropdown should be drawn at all.
  bool get isOpen => _phase != SearchPhase.idle;

  static String _key(String query) =>
      query.trim().split(RegExp(r'\s+')).join(' ').toLowerCase();

  /// Call whenever the field's text changes.
  void onQueryChanged(String text) {
    final key = _key(text);
    _timer?.cancel();

    if (key.isEmpty) {
      _dismissedFor = null;
      _seq++;
      _set(SearchPhase.idle, const [], '');
      return;
    }

    // The field now holds what a selection put there: stay closed.
    if (key == _dismissedFor) return;
    _dismissedFor = null;

    // Whatever is in flight now answers a query the field no longer holds.
    _seq++;

    // Answered recently: show it now, with no request and no wait.
    final cached = _cache[key];
    if (cached != null) {
      _touch(key, cached);
      _set(SearchPhase.results, cached, key);
      return;
    }

    _pending = key;
    _timer = Timer(debounce, () => _run(key));
  }

  Future<void> _run(String key) async {
    if (key != _pending) return;

    final until = _cooldownUntil;
    if (until != null && _now().isBefore(until)) {
      _set(SearchPhase.unavailable, const [], key);
      return;
    }

    final seq = ++_seq;
    // Keep the previous answer on screen while the next one loads.
    _set(SearchPhase.loading, _results, _resultsQuery);

    _requestCount++;
    final outcome = await fetch(key);

    // A real answer is worth keeping even if typing has moved on: backspacing
    // to this query later should cost nothing.
    if (outcome is SearchResults) {
      _cooldownUntil = null;
      _touch(key, outcome.results);
    }

    // Typing has moved on, or the dropdown was dismissed, since this was sent.
    if (seq != _seq) return;

    switch (outcome) {
      case SearchResults(:final results):
        _set(SearchPhase.results, results, key);
      case SearchUnavailable(:final rateLimited):
        // Never cached: a throttle is not an answer, and remembering it would
        // hide real results until the entry aged out.
        if (rateLimited) _cooldownUntil = _now().add(cooldown);
        _set(SearchPhase.unavailable, const [], key);
    }
  }

  /// Close the dropdown - after a selection, a submit, or a tap elsewhere.
  ///
  /// [currentText] is what the field now holds, so writing a selected ticker
  /// into it does not trigger a search that reopens the dropdown.
  void dismiss({String? currentText}) {
    _timer?.cancel();
    _seq++;
    _pending = '';
    _dismissedFor = currentText == null ? null : _key(currentText);
    _set(SearchPhase.idle, const [], '');
  }

  void _touch(String key, List<CompanySearchResult> results) {
    _cache.remove(key);
    _cache[key] = results;
    while (_cache.length > cacheSize) {
      _cache.remove(_cache.keys.first);
    }
  }

  void _set(
    SearchPhase phase,
    List<CompanySearchResult> results,
    String query,
  ) {
    _phase = phase;
    _results = results;
    _resultsQuery = query;
    notifyListeners();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _seq++;
    super.dispose();
  }
}

/// Whether [text] could be sent to the valuation endpoint as a ticker.
///
/// The field now accepts company names, so "bank of america" can reach the
/// Value button. Sending it would come back as an unknown ticker - true, but
/// unhelpful when the company is sitting in the dropdown. The screen uses this
/// to point at the list instead.
bool looksLikeTicker(String text) =>
    RegExp(r'^[A-Za-z0-9.\-]{1,12}$').hasMatch(text.trim());
