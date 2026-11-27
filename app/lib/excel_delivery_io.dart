import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import 'excel_delivery_outcome.dart';

/// Android, iOS and desktop: write the workbook to the app's own documents
/// directory - which needs no storage permission, and which the share sheet
/// can read through the plugin's file provider - then offer the share sheet.
Future<DeliveryOutcome> deliverWorkbook({
  required List<int> bytes,
  required String filename,
  required String ticker,
  required String mimeType,
  /// What the share sheet calls it. Defaults to the workbook wording,
  /// because that is what every existing caller means - but the account
  /// screen hands this same path a JSON export of someone's own data, and
  /// "AAPL DCF model" would be the wrong thing to put on it.
  String? subject,
  String? text,
  void Function(String message)? announce,
}) async {
  final shareSubject = subject ?? '$ticker DCF model';
  final shareText = text ?? 'DCF model for $ticker';
  try {
    final dir = await getApplicationDocumentsDirectory();
    final file = File('${dir.path}/$filename');
    await file.writeAsBytes(bytes, flush: true);

    announce?.call('Saved $filename (${(bytes.length / 1024).round()} KB)');

    await SharePlus.instance.share(
      ShareParams(
        files: [XFile(file.path, mimeType: mimeType)],
        subject: shareSubject,
        text: shareText,
      ),
    );
    return const DeliveryDone();
  } catch (e) {
    debugPrint('saving or sharing $filename failed: $e');
    return const DeliveryFailure(
      'The spreadsheet downloaded, but it couldn’t be saved or shared. '
      'Please try again.',
    );
  }
}
