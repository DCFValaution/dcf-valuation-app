/// Client for the DCF valuation backend.
///
/// The backend deliberately answers with several distinct outcomes rather than
/// only success-or-error: it refuses to value companies a growth-perpetuity DCF
/// does not suit, and it reports data-plan limits separately from real
/// failures. Those distinctions are the point, so this client models them as
/// separate result types instead of flattening everything into an error string.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'dcf_engine.dart';

/// Where the backend lives, as seen from the app.
///
/// Defaults to the deployed service, because that is what a distributed build
/// has to talk to - a shipped app cannot reach a server on the developer's
/// machine.
///
/// To run against a local backend instead:
///
/// ```
/// flutter run --dart-define=BACKEND_BASE_URL=http://10.0.2.2:8000
/// ```
///
/// 10.0.2.2 is the Android emulator's alias for the *host machine's* loopback:
/// localhost or 127.0.0.1 would resolve to the emulator itself. A physical
/// device needs the host's LAN address, or `adb reverse tcp:8000 tcp:8000`.
const String kBackendBaseUrl = String.fromEnvironment(
  'BACKEND_BASE_URL',
  defaultValue: 'https://dcf-valuation-api.onrender.com',
);

/// The outcome of a valuation request.
sealed class ValuationResult {
  const ValuationResult();
}

/// One assumption behind a valuation, with where its value came from.
///
/// [source] is the backend's own label - "derived", "derived (clamped)",
/// "default" or "override". It matters as much as the number: a figure read
/// off the company's filings and a global placeholder deserve different
/// weight, and the backend is careful to distinguish them.
class Assumption {
  final String name;
  final double value;
  final String source;
  final String detail;
  final bool isPercent;

  const Assumption({
    required this.name,
    required this.value,
    required this.source,
    required this.detail,
    required this.isPercent,
  });

  bool get isOverridden => source == 'override';

  factory Assumption.fromJson(Map<String, dynamic> json) => Assumption(
        name: json['name'] as String? ?? '',
        value: (json['value'] as num?)?.toDouble() ?? 0,
        source: json['source'] as String? ?? '',
        detail: json['detail'] as String? ?? '',
        isPercent: json['is_percent'] as bool? ?? false,
      );
}

/// A company was valued.
class ValuationSuccess extends ValuationResult {
  final String ticker;
  final String companyName;
  final String sector;
  final double intrinsicValuePerShare;
  final double currentPrice;

  /// Fraction, e.g. -0.674 means 67.4% downside.
  final double upsideDownside;

  /// The backend's caveat that this figure follows from adjustable
  /// assumptions. Shown verbatim - it is not decoration.
  final String note;

  /// Every assumption behind this valuation, in the backend's order.
  final List<Assumption> assumptions;

  /// The company's reported starting point, as the backend mapped it.
  ///
  /// Carried so the app can recompute locally while a slider is dragged
  /// without re-deriving anything itself.
  final BaseYearData baseYear;

  const ValuationSuccess({
    required this.ticker,
    required this.companyName,
    required this.sector,
    required this.intrinsicValuePerShare,
    required this.currentPrice,
    required this.upsideDownside,
    required this.note,
    required this.assumptions,
    required this.baseYear,
  });

  bool get isUndervalued => upsideDownside > 0;

  Map<String, double> get valuesByName =>
      {for (final a in assumptions) a.name: a.value};

  Assumption? assumption(String name) {
    for (final a in assumptions) {
      if (a.name == name) return a;
    }
    return null;
  }

  factory ValuationSuccess.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>;
    // Global levers (equity risk premium, risk-free rate) arrive in their own
    // list; merge them in so any adjustable input can be looked up by name.
    final all = <Assumption>[
      ...(json['assumptions'] as List<dynamic>? ?? const [])
          .map((a) => Assumption.fromJson(a as Map<String, dynamic>)),
      ...(json['global_levers'] as List<dynamic>? ?? const [])
          .map((a) => Assumption.fromJson(a as Map<String, dynamic>)),
    ];
    // Deduplicate: terminal_growth appears in both lists.
    final seen = <String>{};
    final merged = <Assumption>[
      for (final a in all)
        if (seen.add(a.name)) a
    ];

    return ValuationSuccess(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      sector: company['sector'] as String? ?? '',
      intrinsicValuePerShare:
          (json['intrinsic_value_per_share'] as num).toDouble(),
      currentPrice: (json['current_price'] as num).toDouble(),
      upsideDownside: (json['upside_downside'] as num).toDouble(),
      note: json['note'] as String? ?? '',
      assumptions: merged,
      baseYear:
          BaseYearData.fromJson(json['base_year'] as Map<String, dynamic>),
    );
  }
}

/// The backend refused to value this company, and said why.
///
/// This is not an error. It is a deliberate answer: for a loss-making company
/// or a bank, a growth-perpetuity DCF would produce a confident but meaningless
/// number, so no figure is returned at all.
class ValuationNotSuitable extends ValuationResult {
  final String ticker;
  final String companyName;
  final String message;
  final List<String> reasons;

  const ValuationNotSuitable({
    required this.ticker,
    required this.companyName,
    required this.message,
    required this.reasons,
  });

  factory ValuationNotSuitable.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    return ValuationNotSuitable(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      message: json['message'] as String? ?? 'A standard DCF is not suitable.',
      reasons: (json['reasons'] as List<dynamic>? ?? const [])
          .map((r) => r.toString())
          .toList(),
    );
  }
}

/// Anything that stopped a valuation being produced.
///
/// [kind] drives presentation: an unknown ticker is the user's typo, a plan
/// limit is a subscription matter, and an upstream failure is nobody's fault -
/// all three deserve different wording.
enum ValuationFailureKind {
  tickerNotFound,
  planLimited,
  rateLimited,
  badRequest,
  backendUnreachable,
  upstreamError,
  unexpected,
}

class ValuationFailure extends ValuationResult {
  final ValuationFailureKind kind;
  final String message;

  const ValuationFailure(this.kind, this.message);
}

/// The outcome of an Excel export request.
sealed class ExcelResult {
  const ExcelResult();
}

/// The workbook came back and is ready to be written to disk.
class ExcelSuccess extends ExcelResult {
  final List<int> bytes;
  final String filename;
  const ExcelSuccess(this.bytes, this.filename);
}

/// The export failed, for the same reasons a valuation can.
///
/// Reuses [ValuationFailureKind] so a plan limit or a refusal reads the same
/// whether the user asked for a number or a spreadsheet.
class ExcelFailure extends ExcelResult {
  final ValuationFailureKind kind;
  final String message;
  const ExcelFailure(this.kind, this.message);
}

/// The backend refused to build a workbook because a DCF does not suit the
/// company - the same guard that withholds a valuation.
class ExcelNotSuitable extends ExcelResult {
  final String message;
  const ExcelNotSuitable(this.message);
}

class ValuationApi {
  ValuationApi({http.Client? client, this.baseUrl = kBackendBaseUrl})
      : _client = client ?? http.Client();

  final http.Client _client;
  final String baseUrl;

  /// Long enough to survive a free-tier cold start.
  ///
  /// The backend sleeps after about fifteen minutes idle and takes roughly a
  /// minute to wake. At the old thirty seconds the first request of a session
  /// failed reliably, which read to the user as a broken app rather than a
  /// sleeping server.
  static const Duration _timeout = Duration(seconds: 90);

  /// Building a workbook does more work than a valuation - it refetches,
  /// re-derives, and writes every formula - so it gets a longer budget, and
  /// it may also be the request that wakes the server.
  static const Duration _excelTimeout = Duration(seconds: 120);

  /// How long a request may run before the UI starts saying the server is
  /// probably waking up. Comfortably past a warm response, well short of the
  /// timeout.
  static const Duration wakingThreshold = Duration(seconds: 4);

  /// Wake a sleeping instance without blocking the user.
  ///
  /// `/health` touches no upstream service, so this returns as soon as the
  /// process is up and costs the backend essentially nothing. Called on app
  /// launch so the spin-up overlaps with the user typing a ticker instead of
  /// being paid for in full by their first valuation.
  ///
  /// Returns true if the server answered. Never throws: failing to wake the
  /// server is not itself an error worth showing anyone, since the real
  /// request that follows will report any genuine problem.
  Future<bool> wakeUp() async {
    try {
      final response =
          await _client.get(Uri.parse('$baseUrl/health')).timeout(_timeout);
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Value [ticker], optionally overriding assumptions.
  ///
  /// With no overrides this is a plain GET and every assumption is the
  /// backend's own. With overrides it POSTs them, and only the named ones
  /// change - everything omitted keeps its derived or default value, and the
  /// response labels each accordingly.
  Future<ValuationResult> value(
    String ticker, {
    Map<String, double> overrides = const {},
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    if (cleaned.isEmpty) {
      return const ValuationFailure(
        ValuationFailureKind.badRequest,
        'Enter a ticker symbol first.',
      );
    }

    http.Response response;
    try {
      if (overrides.isEmpty) {
        final uri =
            Uri.parse('$baseUrl/valuation/${Uri.encodeComponent(cleaned)}');
        response = await _client.get(uri).timeout(_timeout);
      } else {
        response = await _client
            .post(
              Uri.parse('$baseUrl/valuation'),
              headers: const {'Content-Type': 'application/json'},
              body: jsonEncode({'ticker': cleaned, 'overrides': overrides}),
            )
            .timeout(_timeout);
      }
    } on TimeoutException {
      return const ValuationFailure(
        ValuationFailureKind.backendUnreachable,
        'The server did not respond within 90 seconds.\n\n'
        'The free hosting tier puts the server to sleep when it is idle, and '
        'waking it usually takes under a minute. Trying again will often '
        'succeed.',
      );
    } on SocketException {
      return ValuationFailure(
        ValuationFailureKind.backendUnreachable,
        'Could not reach the server.\n\n'
        'Check your internet connection and try again.',
      );
    } catch (e) {
      return ValuationFailure(
        ValuationFailureKind.unexpected,
        'Unexpected problem calling the backend: $e',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      return ValuationFailure(
        ValuationFailureKind.unexpected,
        'The backend returned a response that could not be read '
        '(HTTP ${response.statusCode}).',
      );
    }

    final code = body['code'] as String?;
    final message = body['message'] as String? ?? 'Something went wrong.';

    switch (response.statusCode) {
      case 200:
        try {
          return ValuationSuccess.fromJson(body);
        } catch (e) {
          return ValuationFailure(
            ValuationFailureKind.unexpected,
            'The valuation came back in an unexpected shape: $e',
          );
        }

      case 422:
        // Shared status: a suitability refusal and a request-validation error
        // both land here, separated by `code`.
        if (code == 'not_suitable') {
          return ValuationNotSuitable.fromJson(body);
        }
        return ValuationFailure(ValuationFailureKind.badRequest, message);

      case 404:
        return ValuationFailure(
          ValuationFailureKind.tickerNotFound,
          '$cleaned was not found.\n\nCheck the spelling. Note that ETFs, '
          'funds and recently listed companies often have no filings to value.',
        );

      case 402:
        return ValuationFailure(ValuationFailureKind.planLimited, message);

      case 429:
        // Shared status, separated by `code`: "rate_limited" is our own
        // server asking this user to slow down, "upstream_rate_limited" is
        // the market data provider throttling everybody. The user can act on
        // the first and can only wait out the second, so they read
        // differently.
        return ValuationFailure(
          ValuationFailureKind.rateLimited,
          code == 'rate_limited'
              ? 'You’re going a little fast.\n\nThis is a free service with a '
                  'shared limit, so it asks for a short pause after about 30 '
                  'valuations a minute. Wait a moment and try again.'
              : 'The market data provider is rate-limiting requests right '
                  'now.\n\nThis affects everyone using the service, not just '
                  'you. Wait a moment and try again.',
        );

      case 400:
        return ValuationFailure(ValuationFailureKind.badRequest, message);

      case 502:
        return ValuationFailure(
          ValuationFailureKind.upstreamError,
          'The backend could not reach the market data provider.\n\n$message',
        );

      default:
        return ValuationFailure(
          ValuationFailureKind.unexpected,
          'Unexpected response from the backend (HTTP ${response.statusCode}).',
        );
    }
  }

  /// Download the .xlsx DCF model for [ticker], with [overrides] applied so
  /// the workbook matches what is on screen.
  ///
  /// The workbook is always built by the backend, never from the local preview
  /// engine: a spreadsheet outlives the screen that produced it, and it must
  /// carry the authoritative figures with their provenance and sources.
  Future<ExcelResult> downloadExcel(
    String ticker, {
    Map<String, double> overrides = const {},
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    if (cleaned.isEmpty) {
      return const ExcelFailure(
        ValuationFailureKind.badRequest,
        'Enter a ticker symbol first.',
      );
    }

    http.Response response;
    try {
      if (overrides.isEmpty) {
        response = await _client
            .get(Uri.parse(
                '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}/excel'))
            .timeout(_excelTimeout);
      } else {
        response = await _client
            .post(
              Uri.parse('$baseUrl/valuation/excel'),
              headers: const {'Content-Type': 'application/json'},
              body: jsonEncode({'ticker': cleaned, 'overrides': overrides}),
            )
            .timeout(_excelTimeout);
      }
    } on TimeoutException {
      return const ExcelFailure(
        ValuationFailureKind.backendUnreachable,
        'The server did not return the workbook within two minutes.\n\n'
        'If the server had gone to sleep it may still be waking up; '
        'try the export again.',
      );
    } on SocketException {
      return ExcelFailure(
        ValuationFailureKind.backendUnreachable,
        'Could not reach the server to build the workbook.\n\n'
        'Check your internet connection and try again.',
      );
    } catch (e) {
      return ExcelFailure(
        ValuationFailureKind.unexpected,
        'Unexpected problem downloading the workbook: $e',
      );
    }

    if (response.statusCode == 200) {
      // Guard against a JSON error body arriving with a 200 by checking the
      // zip magic number - a .xlsx is a zip, and writing anything else to a
      // .xlsx file would produce a download that silently fails to open.
      final bytes = response.bodyBytes;
      if (bytes.length < 2 || bytes[0] != 0x50 || bytes[1] != 0x4B) {
        return const ExcelFailure(
          ValuationFailureKind.unexpected,
          'The backend returned something that is not a valid .xlsx file.',
        );
      }
      return ExcelSuccess(bytes, _filenameFrom(response, cleaned));
    }

    // Errors come back as JSON, in the same shape the valuation uses.
    String message = 'The workbook could not be produced.';
    String? code;
    try {
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      code = body['code'] as String?;
      message = (body['message'] as String?) ??
          ((body['reasons'] as List<dynamic>?)?.join('\n') ?? message);
    } catch (_) {
      // Leave the default message.
    }

    return switch (response.statusCode) {
      422 when code == 'not_suitable' => ExcelNotSuitable(message),
      422 => ExcelFailure(ValuationFailureKind.badRequest, message),
      404 => ExcelFailure(ValuationFailureKind.tickerNotFound,
          '$cleaned was not found, so there is nothing to export.'),
      402 => ExcelFailure(ValuationFailureKind.planLimited, message),
      429 when code == 'rate_limited' => const ExcelFailure(
          ValuationFailureKind.rateLimited,
          'You’re going a little fast. This is a free service with a shared '
          'limit — wait a moment and try the export again.'),
      429 => const ExcelFailure(ValuationFailureKind.rateLimited,
          'The market data provider is rate-limiting requests right now. '
          'Try the export again shortly.'),
      502 => ExcelFailure(ValuationFailureKind.upstreamError, message),
      _ => ExcelFailure(ValuationFailureKind.unexpected,
          'Unexpected response building the workbook (HTTP ${response.statusCode}).'),
    };
  }

  /// Prefer the filename the backend chose, falling back to the ticker.
  String _filenameFrom(http.Response response, String ticker) {
    final disposition = response.headers['content-disposition'];
    if (disposition != null) {
      final match =
          RegExp(r'filename="?([^";]+)"?').firstMatch(disposition);
      final name = match?.group(1)?.trim();
      if (name != null && name.isNotEmpty) return name;
    }
    return '${ticker}_DCF_Model.xlsx';
  }

  void dispose() => _client.close();
}
