/// Everything a signed-in reader keeps: a watchlist and saved valuations.
///
/// The app talks to Firestore directly rather than through our backend, and
/// that is a performance decision with a measurement behind it. The free
/// Render instance stops when idle; waking it took 19.4 seconds. A
/// watchlist that waited on that would be unusable, and it does not need
/// to: the rows a reader saw last time are their own data, so they come
/// straight from Firestore and paint before anything else is touched.
///
/// The valuations behind those rows still come from the API, in the
/// background, shared across every reader by the day cache in
/// `watchlist.py`. So the screen is never empty and never wrong about
/// when it last heard anything.
///
/// WHAT IS STORED HERE IS THE READER'S OWN COPY
/// --------------------------------------------
/// `lastValue` is what this reader's device last saw, not a figure anyone
/// else is served and not a source of truth. Nobody else can read it - the
/// rules see to that - and nothing trusts it beyond "this is what the
/// screen said, and this is when".
library;

import 'dart:async';

import 'package:cloud_firestore/cloud_firestore.dart';

import 'saved_valuation.dart';

/// One line of the watchlist as the reader last saw it.
class WatchlistEntry {
  const WatchlistEntry({
    required this.ticker,
    this.name,
    this.lastValue,
    this.lastPrice,
    this.asOf,
    this.refused = false,
  });

  final String ticker;
  final String? name;

  /// What the screen said last time. Null for a company only just added,
  /// which renders as "not valued yet" rather than as a blank.
  final double? lastValue;
  final double? lastPrice;

  /// When. Shown beside the figures, because a number without its date is
  /// a claim about now that nobody checked.
  final DateTime? asOf;

  /// True when the company was refused a valuation. Kept so the row can
  /// say so immediately rather than showing an empty figure until the
  /// refresh lands and explains it.
  final bool refused;

  double? get upside => (lastValue != null && (lastPrice ?? 0) > 0)
      ? lastValue! / lastPrice! - 1
      : null;

  Map<String, Object?> toJson() => {
        if (name != null) 'name': name,
        if (lastValue != null) 'value': lastValue,
        if (lastPrice != null) 'price': lastPrice,
        if (asOf != null) 'as_of': asOf!.toUtc().toIso8601String(),
        if (refused) 'refused': true,
      };

  static WatchlistEntry fromJson(String ticker, Object? raw) {
    final map = raw is Map ? raw : const {};
    return WatchlistEntry(
      ticker: ticker,
      name: map['name'] as String?,
      lastValue: (map['value'] as num?)?.toDouble(),
      lastPrice: (map['price'] as num?)?.toDouble(),
      asOf: DateTime.tryParse('${map['as_of'] ?? ''}'),
      refused: map['refused'] == true,
    );
  }
}

/// A saved valuation, as the list shows it before it is opened.
class SavedRef {
  const SavedRef({
    required this.id,
    required this.name,
    required this.ticker,
    required this.schemaVersion,
    this.savedValue,
    this.updatedAt,
  });

  final String id;
  final String name;
  final String ticker;
  final int schemaVersion;

  /// The figure at the moment it was saved, kept for the comparison only.
  /// Opening a save recomputes from today's market data.
  final double? savedValue;
  final DateTime? updatedAt;

  bool get fromAnOlderModel => schemaVersion < kSchemaVersion;
  bool get fromANewerBuild => schemaVersion > kSchemaVersion;
}

/// Turn the stored watchlist document into rows.
///
/// A free function rather than a private method on the Firestore store,
/// because it is the only piece of real logic in the reading path and a
/// method reachable only through a live Firestore is a method no test
/// touches. Mutation testing found exactly that: breaking this was
/// invisible, because every watchlist test ran against the in-memory
/// store and never came near it.
List<WatchlistEntry> watchlistFromDocument(Map<String, dynamic>? data) {
  final doc = data ?? const {};
  final tickers = (doc['tickers'] as List?)?.whereType<String>().toList() ??
      const <String>[];
  final rows = (doc['rows'] as Map?) ?? const {};
  // Order comes from `tickers`, which is the reader's. `rows` is a map and
  // has no order of its own — and a ticker with no cached row still gets
  // an entry, so a company only just added shows as "not valued yet"
  // rather than vanishing until the first refresh lands.
  return [
    for (final ticker in tickers) WatchlistEntry.fromJson(ticker, rows[ticker]),
  ];
}

abstract class UserDataStore {
  /// The watchlist, and every change to it.
  Stream<List<WatchlistEntry>> watchlist();

  Future<void> addTicker(String ticker);
  Future<void> removeTicker(String ticker);

  /// Store what the screen now says, so the next open paints instantly.
  Future<void> cacheRows(List<WatchlistEntry> rows);

  Stream<List<SavedRef>> saves();

  /// Write a save. `id` null creates one.
  Future<String> putSave({
    String? id,
    required String name,
    required String ticker,
    required Map<String, double> assumptions,
    double? savedValue,
  });

  /// Read a save back, migrated into today's shape and labelled.
  Future<LoadedSave> openSave(String id);

  /// Change only the name.
  ///
  /// Deliberately not `putSave` with the old assumptions read back and
  /// written again: that would stamp the current schema version onto a
  /// save the reader has not reopened, quietly claiming they accepted a
  /// shape they have never seen. Renaming is not accepting.
  Future<void> renameSave(String id, String name);

  Future<void> deleteSave(String id);
}

// ---------------------------------------------------------------------------
// Firestore
// ---------------------------------------------------------------------------

class FirestoreUserDataStore implements UserDataStore {
  FirestoreUserDataStore({required this.uid, FirebaseFirestore? firestore})
      : _db = firestore ?? FirebaseFirestore.instance;

  final String uid;
  final FirebaseFirestore _db;

  DocumentReference<Map<String, dynamic>> get _root => _db.doc('users/$uid');

  /// ONE document, not one per ticker.
  ///
  /// Firestore bills per document read. A ten-company watchlist opened
  /// three times a day is thirty reads per reader per day as a collection
  /// and three as a single document; at a thousand readers that is the
  /// difference between brushing the free daily quota and using six per
  /// cent of it.
  DocumentReference<Map<String, dynamic>> get _watchlistDoc =>
      _root.collection('lists').doc('watchlist');

  CollectionReference<Map<String, dynamic>> get _saves =>
      _root.collection('saves');

  @override
  Stream<List<WatchlistEntry>> watchlist() =>
      _watchlistDoc.snapshots().map((snap) => watchlistFromDocument(snap.data()));

  @override
  Future<void> addTicker(String ticker) async {
    final clean = ticker.trim().toUpperCase();
    if (clean.isEmpty) return;
    await _db.runTransaction((tx) async {
      final snap = await tx.get(_watchlistDoc);
      final current =
          (snap.data()?['tickers'] as List?)?.cast<String>() ?? <String>[];
      if (current.contains(clean)) return;
      tx.set(_watchlistDoc, {
        'tickers': [...current, clean],
        'updated_at': FieldValue.serverTimestamp(),
      }, SetOptions(merge: true));
    });
  }

  @override
  Future<void> removeTicker(String ticker) async {
    final clean = ticker.trim().toUpperCase();
    await _db.runTransaction((tx) async {
      final snap = await tx.get(_watchlistDoc);
      final data = snap.data() ?? const {};
      final current = (data['tickers'] as List?)?.cast<String>() ?? <String>[];
      if (!current.contains(clean)) return;
      final rows = Map<String, Object?>.from((data['rows'] as Map?) ?? {})
        ..remove(clean);
      tx.set(_watchlistDoc, {
        'tickers': current.where((t) => t != clean).toList(),
        'rows': rows,
        'updated_at': FieldValue.serverTimestamp(),
      }, SetOptions(merge: true));
    });
  }

  @override
  Future<void> cacheRows(List<WatchlistEntry> rows) async {
    if (rows.isEmpty) return;
    await _watchlistDoc.set({
      'rows': {for (final r in rows) r.ticker: r.toJson()},
      'updated_at': FieldValue.serverTimestamp(),
    }, SetOptions(merge: true));
  }

  @override
  Stream<List<SavedRef>> saves() => _saves
      .orderBy('updated_at', descending: true)
      .snapshots()
      .map((q) => q.docs.map(_readSave).toList());

  static SavedRef _readSave(QueryDocumentSnapshot<Map<String, dynamic>> d) {
    final data = d.data();
    return SavedRef(
      id: d.id,
      name: '${data['name'] ?? d.id}',
      ticker: '${data['ticker'] ?? ''}',
      schemaVersion: (data['schema_version'] as num?)?.toInt() ?? 1,
      savedValue: (data['saved_value'] as num?)?.toDouble(),
      updatedAt: (data['updated_at'] as Timestamp?)?.toDate(),
    );
  }

  @override
  Future<String> putSave({
    String? id,
    required String name,
    required String ticker,
    required Map<String, double> assumptions,
    double? savedValue,
  }) async {
    final doc = id == null ? _saves.doc() : _saves.doc(id);
    final existing = id == null ? null : await doc.get();
    await doc.set({
      'name': name,
      'ticker': ticker.toUpperCase(),
      // Stamped on write, always. The rules refuse a save without it
      // rather than leaving a migration to guess which shape it has.
      'schema_version': kSchemaVersion,
      'assumptions': assumptions,
      'saved_value': savedValue,
      'created_at': existing?.data()?['created_at'] ??
          FieldValue.serverTimestamp(),
      'updated_at': FieldValue.serverTimestamp(),
    });
    return doc.id;
  }

  @override
  Future<LoadedSave> openSave(String id) async {
    final snap = await _saves.doc(id).get();
    final data = snap.data() ?? const {};
    final stored = Map<String, Object?>.from(
        (data['assumptions'] as Map?) ?? const {});
    final version = (data['schema_version'] as num?)?.toInt() ?? 1;
    // Migrated in memory. The stored document is NOT rewritten here:
    // accepting the new shape is the reader's act, which happens when
    // they save again.
    return migrate(stored, version);
  }

  @override
  Future<void> renameSave(String id, String name) => _saves.doc(id).set({
        'name': name,
        'updated_at': FieldValue.serverTimestamp(),
      }, SetOptions(merge: true));

  @override
  Future<void> deleteSave(String id) => _saves.doc(id).delete();
}

// ---------------------------------------------------------------------------
// In memory, for tests and for a build with no Firebase
// ---------------------------------------------------------------------------

class FakeUserDataStore implements UserDataStore {
  final _tickers = <String>[];
  final _rows = <String, WatchlistEntry>{};
  final _saves = <String, Map<String, Object?>>{};
  final _watchlistChanges = StreamController<List<WatchlistEntry>>.broadcast();
  final _saveChanges = StreamController<List<SavedRef>>.broadcast();
  int _nextId = 1;

  /// Every call, in order, so a test can prove what the screen asked for.
  final List<String> calls = [];

  void _emit() {
    _watchlistChanges.add(
      [for (final t in _tickers) _rows[t] ?? WatchlistEntry(ticker: t)],
    );
    _saveChanges.add([
      for (final e in _saves.entries)
        SavedRef(
          id: e.key,
          name: '${e.value['name']}',
          ticker: '${e.value['ticker']}',
          schemaVersion: e.value['schema_version'] as int,
          savedValue: e.value['saved_value'] as double?,
        ),
    ]);
  }

  @override
  Stream<List<WatchlistEntry>> watchlist() async* {
    yield [for (final t in _tickers) _rows[t] ?? WatchlistEntry(ticker: t)];
    yield* _watchlistChanges.stream;
  }

  @override
  Future<void> addTicker(String ticker) async {
    calls.add('addTicker:$ticker');
    final clean = ticker.trim().toUpperCase();
    if (clean.isEmpty || _tickers.contains(clean)) return;
    _tickers.add(clean);
    _emit();
  }

  @override
  Future<void> removeTicker(String ticker) async {
    calls.add('removeTicker:$ticker');
    _tickers.remove(ticker.trim().toUpperCase());
    _rows.remove(ticker.trim().toUpperCase());
    _emit();
  }

  @override
  Future<void> cacheRows(List<WatchlistEntry> rows) async {
    calls.add('cacheRows:${rows.length}');
    for (final r in rows) {
      _rows[r.ticker] = r;
    }
    _emit();
  }

  @override
  Stream<List<SavedRef>> saves() async* {
    yield [
      for (final e in _saves.entries)
        SavedRef(
          id: e.key,
          name: '${e.value['name']}',
          ticker: '${e.value['ticker']}',
          schemaVersion: e.value['schema_version'] as int,
          savedValue: e.value['saved_value'] as double?,
        ),
    ];
    yield* _saveChanges.stream;
  }

  @override
  Future<String> putSave({
    String? id,
    required String name,
    required String ticker,
    required Map<String, double> assumptions,
    double? savedValue,
  }) async {
    calls.add('putSave:$ticker');
    final key = id ?? 's${_nextId++}';
    _saves[key] = {
      'name': name,
      'ticker': ticker.toUpperCase(),
      'schema_version': kSchemaVersion,
      'assumptions': assumptions,
      'saved_value': savedValue,
    };
    _emit();
    return key;
  }

  /// Plant a save written under an older model, which no screen can do.
  void seedOldSave(String id, Map<String, Object?> assumptions, int version,
      {String name = 'An old save', String ticker = 'NVDA'}) {
    _saves[id] = {
      'name': name,
      'ticker': ticker,
      'schema_version': version,
      'assumptions': assumptions,
      'saved_value': null,
    };
    _emit();
  }

  @override
  Future<LoadedSave> openSave(String id) async {
    calls.add('openSave:$id');
    final doc = _saves[id];
    if (doc == null) return migrate(const {}, kSchemaVersion);
    return migrate(
      Map<String, Object?>.from(doc['assumptions'] as Map),
      doc['schema_version'] as int,
    );
  }

  @override
  Future<void> renameSave(String id, String name) async {
    calls.add('renameSave:$id');
    final doc = _saves[id];
    if (doc == null) return;
    doc['name'] = name;
    _emit();
  }

  @override
  Future<void> deleteSave(String id) async {
    calls.add('deleteSave:$id');
    _saves.remove(id);
    _emit();
  }

  void dispose() {
    _watchlistChanges.close();
    _saveChanges.close();
  }
}
