import 'dart:js_interop';

import 'package:flutter/foundation.dart';
import 'package:web/web.dart' as web;

import 'excel_delivery_outcome.dart';

/// iOS Safari sets this on `navigator` when the page is running as a
/// home-screen app. It is not part of any standard, so it is read loosely.
extension type _IosNavigator(JSObject _) implements JSObject {
  external JSBoolean? get standalone;
}

bool get _isIos {
  final ua = web.window.navigator.userAgent;
  if (ua.contains('iPhone') || ua.contains('iPad') || ua.contains('iPod')) {
    return true;
  }
  // An iPad on iPadOS reports itself as a Mac; the touch points give it away.
  return ua.contains('Mac') && web.window.navigator.maxTouchPoints > 1;
}

bool get _isStandalone {
  try {
    if (_IosNavigator(web.window.navigator as JSObject).standalone?.toDart ??
        false) {
      return true;
    }
  } catch (_) {
    // Not an iOS browser; the media query below covers the rest.
  }
  try {
    return web.window.matchMedia('(display-mode: standalone)').matches;
  } catch (_) {
    return false;
  }
}

/// The browser: there is no filesystem to write to, so the bytes have to leave
/// the page either through the Web Share sheet or as a download.
///
/// The order matters on an iPhone. A home-screen (standalone) web app runs
/// without the Safari chrome that owns downloads, and an ordinary anchor
/// download there does nothing at all - which is exactly the dead end the user
/// hit. Web Share with a file attached is the path that does work in that
/// context (iOS 16.4 and later), and it opens the same sheet that offers
/// "Save to Files". So: share when the browser says it can share this file,
/// download otherwise, and if neither is possible say plainly where it will
/// work instead.
Future<DeliveryOutcome> deliverWorkbook({
  required List<int> bytes,
  required String filename,
  required String ticker,
  required String mimeType,
  void Function(String message)? announce,
}) async {
  final data = bytes is Uint8List ? bytes : Uint8List.fromList(bytes);
  final parts = <JSAny>[data.toJS].toJS;

  // 1. Web Share level 2, when the browser will take this actual file.
  try {
    final file = web.File(
      parts,
      filename,
      web.FilePropertyBag(type: mimeType),
    );
    final share = web.ShareData(
      files: <web.File>[file].toJS,
      title: '$ticker DCF model',
      text: 'DCF model for $ticker',
    );
    if (web.window.navigator.canShare(share)) {
      await web.window.navigator.share(share).toDart;
      return const DeliveryDone();
    }
  } on Object catch (e) {
    final text = e.toString();
    // The user closing the sheet is not a failure, and must not be reported
    // as one.
    if (text.contains('AbortError') || text.contains('canceled') ||
        text.contains('cancelled')) {
      return const DeliveryCancelled();
    }
    debugPrint('web share of $filename failed, falling back: $e');
  }

  // 2. An ordinary download, which is how every desktop browser and Safari
  //    itself hand a file over.
  if (!(_isIos && _isStandalone)) {
    try {
      final blob = web.Blob(parts, web.BlobPropertyBag(type: mimeType));
      final url = web.URL.createObjectURL(blob);
      final anchor = web.document.createElement('a') as web.HTMLAnchorElement
        ..href = url
        ..download = filename
        ..style.display = 'none';
      web.document.body?.append(anchor);
      anchor.click();
      anchor.remove();
      // Freed once the browser has had the chance to start the download.
      Future<void>.delayed(const Duration(seconds: 30), () {
        web.URL.revokeObjectURL(url);
      });
      return DeliveryDone(
        'Downloaded $filename (${(bytes.length / 1024).round()} KB)',
      );
    } catch (e) {
      debugPrint('browser download of $filename failed: $e');
    }
  }

  // 3. A home-screen app on an older iPhone: it genuinely cannot save a file.
  //    Say where it can be done rather than leaving the user at a dead end.
  if (_isIos && _isStandalone) {
    return const DeliveryInstruction(
      'This home-screen app can’t save files on your version of iOS. Open the '
      'same page in Safari and tap Export there - the spreadsheet will save to '
      'Files.',
    );
  }
  return const DeliveryFailure(
    'The spreadsheet downloaded, but this browser wouldn’t save it. Try again, '
    'or open the app in another browser.',
  );
}
