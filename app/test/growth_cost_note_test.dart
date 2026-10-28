/// The note that explains a figure falling when growth is raised.
///
/// It appears under the growth slider, which is where the user is looking
/// at the moment the number moves the wrong way. It appears for almost no
/// company, and that is the design: shown everywhere it would be decoration.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/valuation_api.dart';

void main() {
  group('parsing', () {
    ValuationSuccess parse(Object? note) => ValuationSuccess.fromJson({
      'company': {'ticker': 'X', 'company_name': 'X', 'sector': 'S'},
      'intrinsic_value_per_share': 10.0,
      'current_price': 10.0,
      'upside_downside': 0.0,
      'note': 'n',
      'assumptions': <dynamic>[],
      'growth_cost_note': note,
    });

    test('a real note survives', () {
      expect(parse('Raising growth lowers this.').growthCostNote,
          'Raising growth lowers this.');
    });

    test('absent reads as absent', () {
      expect(parse(null).growthCostNote, isNull);
    });

    test('and so does an empty one', () {
      // A backend that sends "" means "nothing to say"; rendering an empty
      // card would be worse than rendering none.
      expect(parse('   ').growthCostNote, isNull);
    });
  });
}
