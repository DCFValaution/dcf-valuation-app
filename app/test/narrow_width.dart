/// The measuring instrument for narrow-width layout, shared by the tests that
/// use it.
///
/// A RenderFlex that overflows reports itself through FlutterError during
/// layout, so the check here is not a screenshot comparison or a guess about
/// pixel counts - it is the framework's own complaint, collected and read
/// back. That makes "does this screen fit" a deterministic question with a
/// yes or no answer at an exact width.
///
/// Two details matter and both were learned the hard way. The collector must
/// restore whatever error handler it replaced, including when the body throws,
/// or one overflowing screen takes the rest of the suite down with it. And an
/// overflow the harness has read must be taken off the binding's own queue,
/// because flutter_test fails a test that ends with an unclaimed exception -
/// which would report the overflow as a crash rather than as the measurement
/// it is.
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// Widths worth holding the app to.
///
/// 360dp is the common compact Android phone and the width the reported
/// overflows came from. 320dp is the narrowest screen still in use - an
/// iPhone SE's predecessors and the small end of Android - and is here
/// because a layout that merely squeezes through 360 tends to fail just
/// below it.
const double kNarrowWidth = 360;
const double kVeryNarrowWidth = 320;
const double kNarrowHeight = 800;

/// Registers the real Roboto that ships with the Flutter SDK.
///
/// This matters more than it looks. The font a widget test gets by default
/// draws every glyph as a full em square, which makes a 17-character label
/// 238 logical pixels wide where real Roboto needs 110 - more than twice as
/// wide. Measuring a layout against that font answers a question no device
/// asks: it reports overflows that do not happen, and it cannot be used to
/// decide whether a label fits on one line.
///
/// Material's English-like typography names Roboto explicitly, so registering
/// it here is enough for most of the app's themed text to pick it up.
///
/// It is not enough for all of it. Some component styles - a SegmentedButton's
/// label among them - resolve to a style with no font family at all, and those
/// still get the default test font. So the measurements here are a mixture:
/// representative for body and title text, and conservative for the rest.
/// That is the right way round for a guard, since anything that fits the
/// wider font certainly fits Roboto - but it does mean this harness cannot
/// answer how many lines a *button label* takes on a device. That question
/// needs a device.
///
/// Returns false when the SDK fonts cannot be found, in which case the
/// default font applies throughout and every measurement is conservative.
Future<bool> useRealFont() async {
  if (_realFontLoaded) return true;
  final root = Platform.environment['FLUTTER_ROOT'];
  if (root == null) return false;
  final dir = Directory('$root/bin/cache/artifacts/material_fonts');
  if (!dir.existsSync()) return false;

  final loader = FontLoader('Roboto');
  var added = 0;
  for (final face in const [
    'roboto-regular',
    'roboto-medium',
    'roboto-bold',
    'roboto-light',
  ]) {
    final file = File('${dir.path}/$face.ttf');
    if (!file.existsSync()) continue;
    loader.addFont(Future.value(file.readAsBytesSync().buffer.asByteData()));
    added++;
  }
  if (added == 0) return false;
  await loader.load();
  _realFontLoaded = true;
  return true;
}

bool _realFontLoaded = false;

/// One overflow, as the framework described it.
class Overflow {
  Overflow(this.summary, this.details);

  /// The first line, which names the widget and the amount.
  final String summary;
  final String details;

  @override
  String toString() => summary;
}

/// Everything that overflowed while [body] ran at a fixed surface size.
///
/// Returns them rather than asserting, so a probe can report every screen in
/// one pass while a test can insist on an empty list.
Future<List<Overflow>> overflowsAt(
  WidgetTester tester,
  double width,
  Future<void> Function() body, {
  double height = kNarrowHeight,
  double textScale = 1.0,
}) async {
  final found = <Overflow>[];

  // Measure against the font a device actually uses, not the test font.
  await useRealFont();

  // A device pixel ratio of 1 makes the physical size and the logical size the
  // same number, so "360" in the test means 360dp on the screen.
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = Size(width, height);
  // Larger system text is the setting under which a layout that merely fits
  // stops fitting, and it is a setting real users turn on.
  tester.platformDispatcher.textScaleFactorTestValue = textScale;

  final previous = FlutterError.onError;
  FlutterError.onError = (FlutterErrorDetails details) {
    final text = details.exceptionAsString();
    if (text.contains('overflowed')) {
      // The message alone says only how far it overflowed. The widget that
      // did it is in the information the framework attaches, which is what
      // makes a failure here actionable rather than a hunt.
      final dump = StringBuffer(text);
      final collector = details.informationCollector;
      if (collector != null) {
        for (final node in collector()) {
          dump.writeln();
          dump.write(node.toStringDeep());
        }
      }
      found.add(Overflow(text.split('\n').first.trim(), dump.toString()));
    } else {
      previous?.call(details);
    }
  };

  try {
    await body();
  } finally {
    FlutterError.onError = previous;
    tester.view.resetPhysicalSize();
    tester.view.resetDevicePixelRatio();
    tester.platformDispatcher.clearTextScaleFactorTestValue();
  }

  // An overflow reported during layout also lands on the binding's pending
  // exception, which would fail the test on its own terms. The harness has
  // already recorded it, so claim it.
  if (found.isNotEmpty) {
    Object? pending = tester.takeException();
    while (pending != null) {
      pending = tester.takeException();
    }
  }

  return found;
}

/// Fails with every overflow listed, rather than only the first.
void expectNoOverflow(List<Overflow> found, String what, double width) {
  if (found.isEmpty) return;
  final lines = found.map((o) => '  - ${o.summary}').join('\n');
  fail('$what overflowed at ${width.toStringAsFixed(0)}dp '
      '(${found.length} ${found.length == 1 ? "place" : "places"}):\n$lines');
}

/// How many lines a piece of text actually took.
///
/// Wrapping is legal layout, so nothing complains about it - but a two-word
/// tab label broken across two lines is exactly the kind of mess an overflow
/// check cannot see. This makes "fits on one line" assertable.
int lineCount(WidgetTester tester, String text) {
  final paragraph = tester.renderObject<RenderParagraph>(find.text(text));
  // The number of distinct vertical offsets among the glyph boxes is the
  // number of lines. Taken this way rather than from line metrics, which
  // this Flutter version does not expose on RenderParagraph.
  final boxes = paragraph.getBoxesForSelection(
    TextSelection(baseOffset: 0, extentOffset: text.length),
  );
  return boxes.map((b) => b.top.round()).toSet().length;
}

/// Whether the text was cut short to fit the space it was given.
///
/// Capping a label at one line stops it wrapping, but a cap alone would just
/// trade a wrap for a quiet truncation - "Relative (mark" is not better than
/// a label on two lines. This is the check that the label genuinely fits.
bool isTruncated(WidgetTester tester, String text) {
  final paragraph = tester.renderObject<RenderParagraph>(find.text(text));
  return paragraph.didExceedMaxLines;
}

/// Anything painted outside the screen, which an overflow report does not
/// always cover - a fixed-width box inside a scroll view clips silently.
List<String> clippedHorizontally(WidgetTester tester, double width) {
  final out = <String>[];
  for (final element in find.byType(Text).evaluate()) {
    final box = element.renderObject as RenderBox?;
    if (box == null || !box.hasSize || box.size.isEmpty) continue;
    // The rect must be transformed, not just the origin. The sensitivity
    // table scales itself down inside a FittedBox, so adding an unscaled
    // width to a transformed corner reports a table that fits perfectly well
    // as hanging off the screen.
    final rect = MatrixUtils.transformRect(
      box.getTransformTo(null),
      Offset.zero & box.size,
    );
    final left = rect.left;
    final right = rect.right;
    // Half a logical pixel of slack: a centred odd-width box can land on a
    // fraction without anything being visibly cut off.
    if (left < -0.5 || right > width + 0.5) {
      final widget = element.widget as Text;
      final label = widget.data ?? '(rich text)';
      out.add('"${label.length > 48 ? '${label.substring(0, 48)}...' : label}" '
          'spans ${left.toStringAsFixed(1)}..${right.toStringAsFixed(1)}');
    }
  }
  return out;
}
