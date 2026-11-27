/// Google's sign-in button, to Google's specification.
///
/// WHY THIS IS NOT JUST A FilledButton WITH AN ICON
/// ------------------------------------------------
/// Google's branding guidelines govern this control, and they are not
/// stylistic advice: the mark must be their supplied asset, unaltered and
/// unrecoloured; the wording must be one of theirs; and the button has a
/// minimum height, a minimum clear space around the mark, and two
/// sanctioned shapes - 4dp corners, or a pill.
///
/// So the asset is bundled rather than drawn (see assets/google_g.png and
/// the note in pubspec.yaml), the only thing done to it is scaling, and
/// the colours below are Google's own values for the light and dark
/// variants rather than this app's palette. It is the one control in the
/// app that deliberately does not match the rest of it.
///
/// The shape is the pill, which is the sanctioned option closest to the
/// app's own language.
library;

import 'package:flutter/material.dart';

import 'theme.dart';

/// Google's light and dark button colours, from their guidelines.
///
/// Named rather than inlined so it is obvious at a glance that these are
/// somebody else's values and are not to be "fixed" to match the theme.
abstract final class _GoogleButton {
  static const lightSurface = Color(0xFFFFFFFF);
  static const lightBorder = Color(0xFF747775);
  static const lightText = Color(0xFF1F1F1F);

  static const darkSurface = Color(0xFF131314);
  static const darkBorder = Color(0xFF8E918F);
  static const darkText = Color(0xFFE3E3E3);

  /// Google's minimum is 40; this is roomier because it is the primary
  /// action on both screens it appears on.
  static const height = 52.0;

  /// Their guidance is 18-20dp for the mark at this button height.
  static const markSize = 20.0;
}

class SignInWithGoogleButton extends StatelessWidget {
  const SignInWithGoogleButton({
    super.key,
    required this.onPressed,
    this.busy = false,
    this.label = 'Sign in with Google',
  });

  /// Null disables it, which is what happens while one is in flight.
  final VoidCallback? onPressed;
  final bool busy;

  /// One of Google's sanctioned strings. "Continue with Google" is the
  /// other; anything else is not allowed.
  final String label;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final surface =
        dark ? _GoogleButton.darkSurface : _GoogleButton.lightSurface;
    final border =
        dark ? _GoogleButton.darkBorder : _GoogleButton.lightBorder;
    final text = dark ? _GoogleButton.darkText : _GoogleButton.lightText;

    return SizedBox(
      height: _GoogleButton.height,
      child: FilledButton(
        key: const Key('sign-in-with-google'),
        onPressed: busy ? null : onPressed,
        style: FilledButton.styleFrom(
          backgroundColor: surface,
          disabledBackgroundColor: surface,
          foregroundColor: text,
          elevation: 0,
          side: BorderSide(color: border),
          shape: const StadiumBorder(),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (busy)
              SizedBox.square(
                dimension: _GoogleButton.markSize,
                child: CircularProgressIndicator(strokeWidth: 2, color: text),
              )
            else
              // Unaltered: scaled, and nothing else. No tint, no colour
              // filter, no rounding of its corners.
              Image.asset(
                'assets/google_g.png',
                width: _GoogleButton.markSize,
                height: _GoogleButton.markSize,
                filterQuality: FilterQuality.high,
              ),
            // Google's minimum clear space around the mark.
            const SizedBox(width: AppSpacing.md),
            Flexible(
              child: Text(
                busy ? 'Signing in…' : label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: context.text.labelLarge?.copyWith(
                  color: text,
                  fontSize: 15,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
