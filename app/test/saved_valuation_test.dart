/// Opening a save written under an older model.
///
/// The model has already changed shape twice in ways that invalidate a
/// stored assumption set, so this is not future-proofing: a save made the
/// week before 1.0.41 refers to three fields that no longer exist.
///
/// Three things can be done with such a save, and two of them are wrong.
/// Refusing to open it loses work the reader did nothing to invalidate.
/// Opening it silently moves the figure and lets the screen imply they
/// chose that. The third is to open it and say what changed, which is what
/// these tests pin — including that the stored document is NOT rewritten on
/// read, because accepting the new shape is the reader's act, not ours.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/saved_valuation.dart';

void main() {
  group('a save written under the current model', () {
    test('comes back untouched and unlabelled', () {
      final stored = {
        'year_one_growth': 0.40,
        'years_two_to_five_growth': 0.20,
        'terminal_roic': 0.25,
      };
      final got = migrate(stored, kSchemaVersion);

      expect(got.assumptions, stored);
      expect(got.notes, isEmpty);
      expect(got.migrated, isFalse);
      expect(got.readOnly, isFalse);
    });
  });

  group('a v1 save, from before the growth structure', () {
    Map<String, Object?> v1() => {
          'revenue_growth': 0.15,
          'operating_margin': 0.30,
          'projection_years': 5,
          'fade_enabled': 1,
          'fade_start_year': 3,
        };

    test('the single growth rate becomes the years 2–5 rate', () {
      // It was a multi-year judgement, which is exactly what years two to
      // five now hold. Year one is left to derive, because a v1 save never
      // made a claim about year one in particular - inventing one would be
      // putting words in the reader's mouth.
      final got = migrate(v1(), 1);

      expect(got.assumptions['years_two_to_five_growth'], 0.15);
      expect(got.assumptions.containsKey('revenue_growth'), isFalse);
      expect(got.assumptions.containsKey('year_one_growth'), isFalse,
          reason: 'year one is derived, not invented from the old rate');
    });

    test('assumptions that still exist are carried straight over', () {
      expect(migrate(v1(), 1).assumptions['operating_margin'], 0.30);
    });

    test('the retired overrides are dropped, not passed on', () {
      // Sending `fade_enabled` to today's backend would be sending an
      // override for a thing that is structural now.
      final got = migrate(v1(), 1).assumptions;

      expect(got.containsKey('projection_years'), isFalse);
      expect(got.containsKey('fade_enabled'), isFalse);
      expect(got.containsKey('fade_start_year'), isFalse);
    });

    test('and every one of them is said out loud', () {
      final fields = migrate(v1(), 1).notes.map((n) => n.field).toList();

      expect(fields, contains('Revenue growth'));
      expect(fields, contains('Forecast length'));
      expect(fields, contains('Growth fade'));
    });

    test('the notes name the figures, not the field names', () {
      // A reader who saved "15% growth over 5 years" should recognise
      // their own numbers in the explanation.
      final notes = migrate(v1(), 1).notes;
      final growth = notes.firstWhere((n) => n.field == 'Revenue growth');
      final length = notes.firstWhere((n) => n.field == 'Forecast length');

      expect(growth.detail, contains('15.0%'));
      expect(growth.detail, contains('years 2–5'));
      expect(length.detail, contains('5-year'));
      expect(length.detail, contains('ten years'));
    });

    test('it is flagged as migrated so the screen can say so', () {
      final got = migrate(v1(), 1);
      expect(got.migrated, isTrue);
      expect(got.savedUnder, 1);
    });

    test('a ten-year v1 save says nothing about the horizon', () {
      // Nothing moved, so there is nothing to report. A migration that
      // announced itself whenever it ran would train people to ignore it.
      final got = migrate({...v1(), 'projection_years': 10}, 1);
      final fields = got.notes.map((n) => n.field);

      expect(fields, isNot(contains('Forecast length')));
    });

    test('a v1 save with the fade off says nothing about the fade', () {
      final got = migrate({...v1(), 'fade_enabled': 0}, 1);
      expect(got.notes.map((n) => n.field), isNot(contains('Growth fade')));
    });
  });

  group('a v2 save', () {
    test('keeps its capital-spending anchor', () {
      // v2 is where the glide arrived; it did not go away.
      final got = migrate({
        'revenue_growth': 0.12,
        'capex_pct': 0.30,
        'capex_terminal_pct': 0.16,
      }, 2);

      expect(got.assumptions['capex_pct'], 0.30);
      expect(got.assumptions['capex_terminal_pct'], 0.16);
    });

    test('still has its single growth rate repointed', () {
      final got = migrate({'revenue_growth': 0.12}, 2);
      expect(got.assumptions['years_two_to_five_growth'], 0.12);
    });
  });

  group('a save from a newer build', () {
    test('opens read-only rather than half-understood', () {
      // A web client can be ahead of an Android one. Interpreting the
      // fields this build happens to recognise and ignoring the rest is
      // the silent-reinterpretation failure wearing a different hat.
      final got = migrate({'something_new': 0.5}, kSchemaVersion + 1);

      expect(got.readOnly, isTrue);
      expect(got.assumptions, isEmpty);
      expect(got.notes.single.detail, contains('newer version'));
      expect(got.notes.single.detail, contains('Nothing has been changed'));
    });

    test('is not reported as migrated, because it was not', () {
      expect(migrate(const {}, kSchemaVersion + 5).migrated, isFalse);
    });
  });

  group('robustness', () {
    test('a value that is not a number is dropped, not crashed on', () {
      // Firestore holds what was written, and a future field could be a
      // string or a map. Losing somebody's whole save to a type error is
      // the worst available outcome.
      final got = migrate({
        'operating_margin': 0.30,
        'note': 'typed by hand',
        'nested': {'a': 1},
      }, kSchemaVersion);

      expect(got.assumptions, {'operating_margin': 0.30});
    });

    test('an integer is read as a rate', () {
      // Firestore returns whole numbers as int, so 1 and 1.0 arrive
      // differently for the same stored value.
      final got = migrate({'margin_convergence_year': 5}, kSchemaVersion);
      expect(got.assumptions['margin_convergence_year'], 5.0);
    });

    test('an empty save is an empty save, not an error', () {
      final got = migrate(const {}, 1);
      expect(got.assumptions, isEmpty);
      expect(got.readOnly, isFalse);
    });
  });
}
