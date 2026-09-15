// The app understands two valuation methods, and must not confuse them.
//
// The backend picks the method itself: a bank is valued on its dividends
// because a growth-perpetuity DCF cannot be applied to one honestly. A DDM
// response carries no `base_year`, so the old parser - which assumed one -
// turned every bank into an error. These tests run against responses recorded
// from the real backend by tools/generate_method_fixtures.py, so they prove the
// app handles what it is actually sent rather than what a test author imagined.

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/assumption_sliders.dart';
import 'package:dcf_app/valuation_api.dart';

/// The recorded case for [ticker], by the label the generator gave it.
Map<String, dynamic> caseFor(String ticker) {
  final file = File('test/fixtures/method_cases.json');
  final cases = jsonDecode(file.readAsStringSync()) as List<dynamic>;
  return cases.cast<Map<String, dynamic>>().firstWhere(
        (c) => c['ticker'] == ticker,
      )['body']
      as Map<String, dynamic>;
}

ValuationSuccess parseSuccess(String ticker) =>
    ValuationSuccess.fromJson(caseFor(ticker));

void main() {
  group('a dividend discount model response', () {
    test('parses at all, which is what a bank used to fail to do', () {
      final jpm = parseSuccess('JPM');

      expect(jpm.method, ValuationMethod.ddm);
      expect(jpm.intrinsicValuePerShare, greaterThan(0));
      expect(jpm.companyName, contains('JPMorgan'));
    });

    test('has no base year, and does not pretend otherwise', () {
      final jpm = parseSuccess('JPM');

      // The DCF's starting point does not exist here. The app must carry the
      // absence rather than invent a zeroed one, which would silently feed
      // nonsense to the local preview engine.
      expect(caseFor('JPM').containsKey('base_year'), isFalse);
      expect(jpm.baseYear, isNull);
      expect(jpm.supportsLocalPreview, isFalse);
    });

    test('carries the dividend it projects from', () {
      final jpm = parseSuccess('JPM');
      final basis = jpm.dividendBasis;

      expect(basis, isNotNull);
      expect(basis!.currentAnnualDividend, greaterThan(0));
      expect(basis.dividendYield, greaterThan(0));
      // The backend's own sentence explaining how it got there, shown verbatim
      // so the figure can be checked against the company's filings.
      expect(basis.detail, isNotEmpty);
    });

    test('carries the honesty note and says why a DCF was not used', () {
      final jpm = parseSuccess('JPM');

      expect(jpm.note, isNotEmpty);
      expect(jpm.note, contains('assumptions'));
      expect(jpm.whyNotDcf, isNotEmpty);
    });

    test('carries the buyback warning, which a bank most needs', () {
      final jpm = parseSuccess('JPM');

      expect(jpm.warnings, isNotEmpty);
      expect(
        jpm.warnings.any((w) => w.contains('buyback')),
        isTrue,
        reason:
            'a dividend model cannot see buybacks, and JPM returns more '
            'cash that way than in dividends - dropping this warning would '
            'leave an understated figure looking authoritative',
      );
    });

    test('an insurer behaves the same way as a bank', () {
      final trv = parseSuccess('TRV');

      expect(trv.method, ValuationMethod.ddm);
      expect(trv.baseYear, isNull);
      expect(trv.dividendBasis, isNotNull);
      expect(trv.note, isNotEmpty);
    });

    test('exposes exactly the levers the DDM accepts', () {
      final jpm = parseSuccess('JPM');

      for (final spec in kDdmAdjustable) {
        expect(
          jpm.assumption(spec.name),
          isNotNull,
          reason: '${spec.name} has a slider but the backend did not return it',
        );
      }
      // And none of the DCF's, which would be rejected as belonging to the
      // other method.
      expect(jpm.assumption('revenue_growth'), isNull);
      expect(jpm.assumption('wacc'), isNull);
    });

    test('every lever arrives with its provenance', () {
      final jpm = parseSuccess('JPM');

      for (final spec in kDdmAdjustable) {
        expect(jpm.assumption(spec.name)!.source, isNotEmpty);
      }
      // The two are told apart on screen, so they must differ in the data.
      expect(jpm.assumption('cost_of_equity')!.source, 'derived');
      expect(jpm.assumption('terminal_growth')!.source, 'default');
    });

    test('every slider sits inside the range the backend derived', () {
      for (final ticker in const ['JPM', 'TRV']) {
        final result = parseSuccess(ticker);
        for (final spec in kDdmAdjustable) {
          final value = result.assumption(spec.name)!.value;
          expect(
            value,
            greaterThanOrEqualTo(spec.min),
            reason: '$ticker ${spec.name} is below its slider',
          );
          expect(
            value,
            lessThanOrEqualTo(spec.max),
            reason: '$ticker ${spec.name} is above its slider',
          );
        }
      }
    });
  });

  group('a discounted cash flow response', () {
    test('is unchanged: still a DCF, still with its base year', () {
      final aapl = parseSuccess('AAPL');

      expect(aapl.method, ValuationMethod.dcf);
      expect(aapl.baseYear, isNotNull);
      expect(aapl.supportsLocalPreview, isTrue);
      expect(aapl.dividendBasis, isNull);
    });

    test('keeps its own levers', () {
      final aapl = parseSuccess('AAPL');

      for (final spec in kAdjustable) {
        expect(aapl.assumption(spec.name), isNotNull);
      }
      expect(aapl.assumption('dividend_growth'), isNull);
    });

    test('a response without a method field is read as a DCF', () {
      // Older deployments do not send `method`. Defaulting to the DCF keeps a
      // mixed-version app working rather than failing on every company.
      final body = Map<String, dynamic>.of(caseFor('AAPL'))..remove('method');

      expect(ValuationSuccess.fromJson(body).method, ValuationMethod.dcf);
    });
  });

  group('the refusal', () {
    test('still refuses, and still says why', () {
      final rivn = ValuationNotSuitable.fromJson(caseFor('RIVN'));

      expect(rivn.ticker, 'RIVN');
      expect(rivn.reasons, isNotEmpty);
      expect(rivn.reasons.first, contains('loss-making'));
    });

    test('reports that a speculative estimate could be asked for', () {
      final rivn = ValuationNotSuitable.fromJson(caseFor('RIVN'));

      expect(rivn.speculativeEstimateAvailable, isTrue);
    });

    test('defaults to false when the field is absent', () {
      // The flag says an estimate can be requested. Absent information must
      // not read as a yes.
      final body = Map<String, dynamic>.of(caseFor('RIVN'))
        ..remove('speculative_estimate_available');

      expect(
        ValuationNotSuitable.fromJson(body).speculativeEstimateAvailable,
        isFalse,
      );
    });
  });

  group('the assumption panel adapts to the method', () {
    test('each method names its own discount rate', () {
      expect(discountRateName(kAdjustable), 'wacc');
      expect(discountRateName(kDdmAdjustable), 'cost_of_equity');
    });

    test('terminal growth is blocked against the right rate', () {
      // Growth at or above the discount rate breaks Gordon growth whichever
      // model is running; the check must follow the method, not the DCF.
      expect(
        growthExceedsDiscountRate({
          'cost_of_equity': 0.09,
          'terminal_growth': 0.09,
        }, kDdmAdjustable),
        isTrue,
      );
      expect(
        growthExceedsDiscountRate({
          'cost_of_equity': 0.09,
          'terminal_growth': 0.02,
        }, kDdmAdjustable),
        isFalse,
      );
      // A DDM's cost of equity must not be mistaken for a missing WACC and
      // waved through.
      expect(
        growthExceedsDiscountRate({
          'cost_of_equity': 0.02,
          'terminal_growth': 0.06,
        }, kAdjustable),
        isFalse,
      );
    });

    test('a year count is not rendered as a percentage', () {
      final years = kDdmAdjustable.firstWhere(
        (s) => s.name == 'high_growth_years',
      );

      expect(years.isPercent, isFalse);
      expect(years.format(5), '5 years');
      expect(years.format(1), '1 year');
      expect(kAdjustable.first.format(0.05), '5.00%');
    });
  });
}
