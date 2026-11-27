/// How many free valuations are left, as the server last said.
///
/// THE APP NEVER DECIDES THIS. The number here is a courtesy: it is what
/// the pill in the header shows, and it is read from response headers the
/// server sets on every valuation. The decision to refuse is the server's
/// alone and arrives as a 403 - see `ValuationGated`.
///
/// That split is the whole design. A client that decided when to show the
/// gate would be a client that could be told not to.
library;

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'account_service.dart';
import 'release_log.dart';

/// The server's answer about this caller's plan.
@immutable
class Allowance {
  const Allowance({
    required this.plan,
    required this.window,
    this.limit,
    this.remaining,
    this.degraded = false,
  });

  /// "anonymous", "free", "unlimited".
  final String plan;

  /// "lifetime" or "day".
  final String window;

  /// Null means there is no limit. Not zero, and not a big number: a limit
  /// that exists and a limit that does not are different claims, and the
  /// pill appears for one and not the other.
  final int? limit;

  final int? remaining;

  /// The server could not read the counter, so [remaining] is a guess.
  /// The pill hides rather than show a number that may be wrong.
  final bool degraded;

  bool get metered => limit != null;

  /// Whether to show the reader anything at all.
  bool get showCount => metered && remaining != null && !degraded;

  /// The last one. The only moment the count earns any emphasis.
  bool get onLastOne => showCount && remaining == 1;

  static const _plan = 'x-plan';
  static const _window = 'x-valuations-window';
  static const _limit = 'x-valuations-limit';
  static const _remaining = 'x-valuations-remaining';
  static const _degraded = 'x-valuations-degraded';

  /// Read it off a response, or null if the server said nothing.
  ///
  /// Null rather than a default: "the server did not mention it" and "the
  /// server said you have none left" must never collapse into the same
  /// value, because one of them hides the pill and the other would show
  /// "0 free valuations left" to somebody who is not metered at all.
  static Allowance? fromHeaders(Map<String, String> headers) {
    // package:http lower-cases header names; be defensive anyway, since a
    // Map from elsewhere need not have.
    final lower = {
      for (final entry in headers.entries)
        entry.key.toLowerCase(): entry.value,
    };
    final plan = lower[_plan];
    if (plan == null) return null;

    final rawLimit = lower[_limit];
    return Allowance(
      plan: plan,
      window: lower[_window] ?? 'lifetime',
      limit: (rawLimit == null || rawLimit == 'none')
          ? null
          : int.tryParse(rawLimit),
      remaining: int.tryParse(lower[_remaining] ?? ''),
      degraded: lower[_degraded] == '1',
    );
  }

  @override
  bool operator ==(Object other) =>
      other is Allowance &&
      other.plan == plan &&
      other.window == window &&
      other.limit == limit &&
      other.remaining == remaining &&
      other.degraded == degraded;

  @override
  int get hashCode => Object.hash(plan, window, limit, remaining, degraded);

  @override
  String toString() =>
      'Allowance($plan, $remaining of $limit per $window'
      '${degraded ? ', degraded' : ''})';
}

/// An [http.Client] that signs every request and reads the allowance back.
///
/// A decorator rather than a change at each call site. The API client makes
/// requests from fifteen places; threading two headers and a response
/// observer through all of them would be fifteen chances to miss one, and
/// the one missed would be an unattributed request that quietly spent the
/// per-address cap instead of the reader's own allowance.
class AuthedClient extends http.BaseClient {
  AuthedClient(
    this._inner, {
    this.tokens,
    this.onAllowance,
  });

  final http.Client _inner;

  /// Where the ID and App Check tokens come from. Null in tests and in a
  /// build with no Firebase, where requests go out unsigned and the server
  /// falls back to its per-address cap.
  final Future<AuthTokens?> Function()? tokens;

  /// Called whenever the server says something about the allowance.
  final void Function(Allowance)? onAllowance;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    try {
      final got = await tokens?.call();
      if (got != null) request.headers.addAll(got.headers);
    } catch (e) {
      // An unsigned request still works; it is simply governed by the
      // per-address cap instead. Failing the request here would turn a
      // token hiccup into a broken app.
      logFailure('auth headers', e);
    }

    final response = await _inner.send(request);

    final allowance = Allowance.fromHeaders(response.headers);
    if (allowance != null) {
      try {
        onAllowance?.call(allowance);
      } catch (e) {
        logFailure('allowance listener', e);
      }
    }
    return response;
  }

  @override
  void close() {
    _inner.close();
    super.close();
  }
}
