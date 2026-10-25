/// Presentation-side text tidying for messages written by the backend.
library;

/// Reflows a backend message for a phone screen.
///
/// The backend composes its messages for a terminal: a headline sentence, then
/// continuation lines indented by two spaces. Rendered verbatim in a Text
/// widget that produces ragged output with stray indentation mid-paragraph -
/// most visibly on the plan-limited message, which arrived as three
/// hard-wrapped fragments.
///
/// Blank lines are genuine paragraph breaks and are preserved; single line
/// breaks are joined, since they are wrapping rather than structure. The raw
/// message is left untouched at the API layer so logs and tests still see
/// exactly what the backend sent.
String tidyBackendMessage(String raw) {
  final paragraphs = raw.split(RegExp(r'\n[ \t]*\n'));

  return paragraphs
      .map(
        (paragraph) => paragraph
            .split('\n')
            .map((line) => line.trim())
            .where((line) => line.isNotEmpty)
            .join(' '),
      )
      .where((paragraph) => paragraph.isNotEmpty)
      .join('\n\n');
}

/// Splits a trailing bare URL off a message so it can be styled separately
/// rather than wrapping mid-word through a paragraph.
///
/// Returns the message without the URL, and the URL if one was found.
(String message, String? url) splitTrailingUrl(String text) {
  final match = RegExp(r'(https?://\S+)\s*$').firstMatch(text);
  if (match == null) return (text, null);

  var message = text.substring(0, match.start).trimRight();
  // Tidy the connective left dangling by removing the URL, e.g. "upgrade at".
  message = message.replaceFirst(RegExp(r'[\s,]*\bat\b[\s,:]*$'), '');
  message = message.replaceFirst(RegExp(r'[\s,:]+$'), '');
  return (message, match.group(1));
}

/// Rounds a number for display, with negative zero folded into zero.
///
/// Fixed-point formatting keeps a sign that rounding has already made
/// meaningless: a revenue change of -0.004% is "-0.0%", which reads as a
/// fall the figure itself denies, and -0.0 is "-0.0%" for a number that is
/// not negative at all. Worse where a sign is prefixed by hand, because
/// -0.0 compares equal to 0.0 and so takes the positive branch: the implied
/// upside badge rendered "+-0.0%".
///
/// Anything that rounds to zero is zero.
String fixed(double value, int places) {
  final text = value.toStringAsFixed(places);
  // Catches "-0", "-0.0", "-0.00" without being fooled by "-0.01".
  return RegExp(r'^-0(\.0*)?$').hasMatch(text) ? text.substring(1) : text;
}

/// A percentage, from a fraction, with no false negative zero.
String percent(double fraction, {int places = 1}) =>
    '${fixed(fraction * 100, places)}%';

/// A percentage carrying an explicit sign, as a change figure does.
///
/// The sign is taken from the rounded text rather than from the input, so a
/// value that rounds away to nothing is "0.0%" rather than "+0.0%" claiming
/// a rise that is not there.
String signedPercent(double fraction, {int places = 1}) {
  final text = fixed(fraction * 100, places);
  if (text.startsWith('-')) return '$text%';
  return double.parse(text) == 0 ? '$text%' : '+$text%';
}
