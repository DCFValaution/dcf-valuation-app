/// The Export to Excel button, and the delivery behind it.
///
/// Four screens offer a workbook now - the valuation, the speculative
/// estimate, the user-built hypothetical and the relative view - and they all
/// do the same three things: ask the backend for bytes, hand them to the
/// platform, and say what happened. Written once so a refusal, a cancelled
/// share sheet or an iPhone that cannot save a file reads identically
/// wherever the export was started from.
library;

import 'package:flutter/material.dart';

import 'excel_delivery.dart';
import 'theme.dart';
import 'valuation_api.dart';

/// Runs an export end to end and reports the outcome through [showMessage].
///
/// [fetch] is whichever of the API's download methods belongs to this screen.
/// Returns when the file has been delivered, refused, or failed - so a caller
/// can clear its own busy flag knowing nothing is still in flight.
Future<void> runExport({
  required Future<ExcelResult> Function() fetch,
  required String ticker,
  required void Function(String message, {bool isError}) showMessage,
  required VoidCallback onDone,
}) async {
  final result = await fetch();

  switch (result) {
    case ExcelSuccess(:final bytes, :final filename):
      // Where the bytes go from here depends on the platform: a file and a
      // share sheet on a phone, a download or the Web Share sheet in a
      // browser.
      final outcome = await deliverWorkbook(
        bytes: bytes,
        filename: filename,
        ticker: ticker,
        mimeType: kXlsxMimeType,
        announce: (text) {
          onDone();
          showMessage(text, isError: false);
        },
      );
      onDone();
      switch (outcome) {
        case DeliveryDone(:final message):
          if (message != null) showMessage(message, isError: false);
        case DeliveryCancelled():
          break;
        case DeliveryInstruction(:final message):
          showMessage(message, isError: false);
        case DeliveryFailure(:final message):
          showMessage(message, isError: true);
      }

    case ExcelNotSuitable(:final message):
      onDone();
      showMessage(message, isError: true);

    case ExcelFailure(:final message):
      onDone();
      showMessage(message, isError: true);
  }
}

const String kXlsxMimeType =
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

/// The button itself.
///
/// [caption] differs by screen because what the file contains differs: a
/// valuation's workbook is a model to argue with, a speculative one is a
/// projection that is explicitly not a valuation. The button should not
/// promise the same thing in both places.
class ExportButton extends StatelessWidget {
  const ExportButton({
    super.key,
    required this.busy,
    required this.onPressed,
    this.blocked = false,
    this.blockedReason,
    this.overrideCount = 0,
    this.caption =
        'A spreadsheet with working formulas, matching the assumptions below.',
  });

  final bool busy;
  final bool blocked;
  final String? blockedReason;
  final int overrideCount;
  final String caption;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final label = overrideCount == 0
        ? 'Export to Excel'
        : 'Export to Excel · $overrideCount adjusted';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: (busy || blocked) ? null : onPressed,
            icon: busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.grid_on_rounded, size: 18),
            label: Text(busy ? 'Building workbook…' : label),
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          blocked ? (blockedReason ?? 'Not available right now.') : caption,
          style: context.text.labelSmall?.copyWith(
            color: blocked ? colors.negative : colors.textSecondary,
          ),
        ),
      ],
    );
  }
}
