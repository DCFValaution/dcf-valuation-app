/// A saved valuation, and the versioning that lets an old one still open.
///
/// WHY A VERSION NUMBER, CONCRETELY
/// --------------------------------
/// This is not hypothetical future-proofing. The model has already changed
/// shape twice in ways that would break a stored assumption set:
///
///   v1  one `revenue_growth` held flat, plus `projection_years` and a
///       `fade_enabled` / `fade_start_year` override
///   v2  adds the capital-spending glide (`capex_terminal_pct`)
///   v3  three growth rates (`year_one_growth`, `years_two_to_five_growth`
///       and a structural decline), tax convergence, a terminal return on
///       capital, an optional target margin, and a reinvestment method
///
/// A save made the week before v3 shipped refers to three fields that no
/// longer exist. There are three things you can do with it, and two of
/// them are wrong:
///
///   refuse to open it            the reader loses work they did nothing
///                                to invalidate
///   open it silently             the figure changes and the screen
///                                implies they chose that
///   open it and say what moved   this
///
/// So every save carries the version it was written under, migration is a
/// pure function per step, and the result is LABELLED: the screen says
/// "saved under v1, reopened as v3" and lists what no longer applies. The
/// stored document is never rewritten on read — only when the reader saves
/// again, which is the moment they accept the new shape.
///
/// A save from a FUTURE version opens read-only. A web client can be ahead
/// of an Android one, and half-interpreting a shape this build does not
/// know is the silent-reinterpretation failure wearing a different hat.
library;

/// The shape this build writes.
const int kSchemaVersion = 3;

/// What a migration had to do to an old save.
class MigrationNote {
  const MigrationNote({required this.field, required this.detail});

  /// The assumption this is about, as the reader saw it named.
  final String field;

  /// One sentence, for a person.
  final String detail;
}

/// The outcome of reading a stored save.
class LoadedSave {
  const LoadedSave({
    required this.assumptions,
    required this.savedUnder,
    required this.notes,
    this.readOnly = false,
  });

  /// The assumptions in today's shape, ready to send as overrides.
  final Map<String, double> assumptions;

  /// The version the document was written under.
  final int savedUnder;

  /// What changed on the way here. Empty when nothing did.
  final List<MigrationNote> notes;

  /// True when the save came from a build newer than this one.
  final bool readOnly;

  bool get migrated => savedUnder != kSchemaVersion && !readOnly;
}

/// Read a stored assumption map, whatever version wrote it.
///
/// Never throws for an unfamiliar shape: a save that cannot be understood
/// opens read-only with a note, because losing somebody's work to an
/// exception is the worst of the available outcomes.
LoadedSave migrate(Map<String, Object?> stored, int savedUnder) {
  if (savedUnder > kSchemaVersion) {
    return LoadedSave(
      assumptions: const {},
      savedUnder: savedUnder,
      readOnly: true,
      notes: const [
        MigrationNote(
          field: 'this save',
          detail: 'It was made in a newer version of Intrinsic. Update the '
              'app to open it. Nothing has been changed or lost.',
        ),
      ],
    );
  }

  final out = <String, double>{};
  for (final entry in stored.entries) {
    final value = entry.value;
    if (value is num) out[entry.key] = value.toDouble();
  }
  final notes = <MigrationNote>[];

  if (savedUnder <= 1) _v1ToV2(out, notes);
  if (savedUnder <= 2) _v2ToV3(out, notes);

  return LoadedSave(
    assumptions: out,
    savedUnder: savedUnder,
    notes: notes,
  );
}

/// v1 → v2. The capital-spending glide arrived; nothing was removed.
void _v1ToV2(Map<String, double> a, List<MigrationNote> notes) {
  // A v1 save set one capital-spending rate and the model held it flat.
  // v2 glides from it to an anchor. Leaving `capex_terminal_pct` unset
  // means the glide is derived, which is the current default and the
  // honest reading of a save that never had an opinion about it.
}

/// v2 → v3. The growth structure, and three overrides that stopped
/// existing.
void _v2ToV3(Map<String, double> a, List<MigrationNote> notes) {
  final oneRate = a.remove('revenue_growth');
  if (oneRate != null) {
    // The single rate becomes the years 2-5 rate: it was a multi-year
    // judgement, which is what years two to five now hold. Year one is
    // left to derive from the latest financial year, because a v1 save
    // never made a claim about year one in particular.
    a['years_two_to_five_growth'] = oneRate;
    notes.add(MigrationNote(
      field: 'Revenue growth',
      detail: 'Your single growth rate of ${_pct(oneRate)} now applies to '
          'years 2–5. Year one uses the latest financial year’s own '
          'growth, which the model did not separate when you saved this.',
    ));
  }

  final years = a.remove('projection_years');
  if (years != null && years.round() != 10) {
    notes.add(MigrationNote(
      field: 'Forecast length',
      detail: 'This save set a ${years.round()}-year forecast. The model '
          'uses ten years for every company now, so the figure has moved. '
          'A shorter forecast does not make a model more honest — it moves '
          'value into the perpetuity, which is the part nobody can check.',
    ));
  }

  final fadeOn = a.remove('fade_enabled');
  final fadeStart = a.remove('fade_start_year');
  if (fadeOn != null && fadeOn != 0) {
    notes.add(MigrationNote(
      field: 'Growth fade',
      detail: fadeStart == null
          ? 'The growth fade you switched on is part of the model now, so '
              'there is nothing left to switch.'
          : 'The growth fade you started at year ${fadeStart.round()} is '
              'part of the model now: growth declines from year six to the '
              'terminal rate for every company.',
    ));
  }
}

String _pct(double value) => '${(value * 100).toStringAsFixed(1)}%';
