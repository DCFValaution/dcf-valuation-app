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
      .map((paragraph) => paragraph
          .split('\n')
          .map((line) => line.trim())
          .where((line) => line.isNotEmpty)
          .join(' '))
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
