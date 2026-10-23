/// Shared building blocks, so every screen is assembled from the same parts.
///
/// The app grew one feature at a time, and each feature brought its own
/// notice box, card and badge - five slightly different ways to show a
/// warning, with different padding, radius and icon size. These are the one
/// way each is now drawn.
///
/// COLOUR ROLES, which these components encode so screens cannot mix them up:
///
///   * positive / negative - a real valuation's upside or downside, and a
///     genuine error. Nothing else.
///   * caution (amber) - speculation, and things to keep in mind.
///   * neutral - relative, market-based figures and supporting information.
///   * accent (brand) - what can be tapped or is selected. Never a verdict.
library;

import 'package:flutter/material.dart';
import 'package:flutter/physics.dart' show SpringSimulation;

import 'formatting.dart';
import 'theme.dart';
import 'valuation_api.dart' show CapitalReturns, CapitalVerdict;

/// The meaning a tinted surface carries.
enum Tone { neutral, accent, caution, negative, positive }

extension ToneColors on Tone {
  Color foreground(BuildContext context) {
    final c = context.colors;
    return switch (this) {
      Tone.neutral => c.textSecondary,
      Tone.accent => context.scheme.primary,
      Tone.caution => c.caution,
      Tone.negative => c.negative,
      Tone.positive => c.positive,
    };
  }

  Color surface(BuildContext context) {
    final c = context.colors;
    return switch (this) {
      Tone.neutral => context.scheme.surfaceContainerHighest,
      Tone.accent => c.accentSurface,
      Tone.caution => c.cautionSurface,
      Tone.negative => c.negativeSurface,
      Tone.positive => c.positiveSurface,
    };
  }
}

/// A content card: the surface every grouped block of information sits on.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.xl),
    this.tone,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  /// Tints the card for a card whose whole meaning is a tone - a refusal, a
  /// speculative figure. Most cards are plain.
  final Tone? tone;

  @override
  Widget build(BuildContext context) {
    final tone = this.tone;
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: tone == null ? context.scheme.surface : tone.surface(context),
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(
          color: tone == null
              ? context.colors.hairline
              : tone.foreground(context).withValues(alpha: 0.28),
        ),
      ),
      child: child,
    );
  }
}

/// A section's heading, with an optional line under it and an action beside it.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.trailing,
    this.badge,
  });

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final Widget? badge;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            // The title and badge share one expanding slot, so the empty space
            // shrinks before the title wraps.
            Expanded(
              child: Row(
                children: [
                  Flexible(child: Text(title, style: context.text.titleMedium)),
                  if (badge != null) ...[
                    const SizedBox(width: AppSpacing.sm),
                    // Flexible as well as the title: a badge naming the peer
                    // group's provenance is long enough on its own to push
                    // the header off a compact phone, and the title having
                    // room to shrink does not help if the badge cannot.
                    Flexible(child: badge!),
                  ],
                ],
              ),
            ),
            ?trailing,
          ],
        ),
        if (subtitle != null) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(subtitle!, style: context.text.bodySmall),
        ],
      ],
    );
  }
}

/// The small uppercase label above a figure.
class Eyebrow extends StatelessWidget {
  const Eyebrow(this.text, {super.key, this.color});

  final String text;
  final Color? color;

  @override
  Widget build(BuildContext context) =>
      Text(text, style: context.text.labelMedium?.copyWith(color: color));
}

/// A tinted panel with an icon: a note, a warning, a refusal.
class Callout extends StatelessWidget {
  const Callout({
    super.key,
    required this.text,
    this.tone = Tone.neutral,
    this.icon,
    this.title,
    this.children = const [],
  });

  final String text;
  final Tone tone;
  final IconData? icon;
  final String? title;

  /// Anything below the text, e.g. reasons or an action.
  final List<Widget> children;

  static IconData _defaultIcon(Tone tone) => switch (tone) {
    Tone.caution => Icons.warning_amber_rounded,
    Tone.negative => Icons.error_outline_rounded,
    Tone.positive => Icons.check_circle_outline_rounded,
    Tone.accent || Tone.neutral => Icons.info_outline_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final fg = tone.foreground(context);
    // Neutral notes read as supporting detail; toned ones carry their colour
    // into the text so the meaning survives a glance.
    final bodyColor = tone == Tone.neutral
        ? context.colors.textSecondary
        : (title == null ? fg : context.scheme.onSurface);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: tone.surface(context),
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 1),
            child: Icon(icon ?? _defaultIcon(tone), size: 18, color: fg),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title != null) ...[
                  Text(
                    title!,
                    style: context.text.titleSmall?.copyWith(color: fg),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                ],
                if (text.isNotEmpty)
                  Text(
                    tidyBackendMessage(text),
                    style: context.text.bodySmall?.copyWith(color: bodyColor),
                  ),
                ...children,
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Several notes of one kind, as one grouped list rather than a stack of boxes.
///
/// A company can carry four or five warnings. Drawn as separate tinted boxes
/// they buried the result they qualify; as one list under one heading they
/// read as what they are - the small print, still all of it, in one place.
class NoteList extends StatelessWidget {
  const NoteList({
    super.key,
    required this.title,
    required this.notes,
    this.tone = Tone.caution,
  });

  final String title;
  final List<String> notes;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    if (notes.isEmpty) return const SizedBox.shrink();
    final fg = tone.foreground(context);
    return AppCard(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: fg),
              const SizedBox(width: AppSpacing.sm),
              Expanded(child: Text(title, style: context.text.titleSmall)),
              if (notes.length > 1) Pill(label: '${notes.length}', tone: tone),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          for (final note in notes)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.md),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    margin: const EdgeInsets.only(top: 8, right: AppSpacing.md),
                    width: 5,
                    height: 5,
                    decoration: BoxDecoration(
                      color: fg,
                      shape: BoxShape.circle,
                    ),
                  ),
                  Expanded(
                    child: Text(
                      tidyBackendMessage(note),
                      style: context.text.bodySmall?.copyWith(
                        color: context.scheme.onSurface,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// A small rounded label: a method, a mode, a count.
class Pill extends StatelessWidget {
  const Pill({
    super.key,
    required this.label,
    this.tone = Tone.neutral,
    this.icon,
    this.outlined = false,
  });

  final String label;
  final Tone tone;
  final IconData? icon;

  /// Outlined for emphasis without fill, e.g. NOT A VALUATION.
  final bool outlined;

  @override
  Widget build(BuildContext context) {
    final fg = tone.foreground(context);
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm + 2,
        vertical: 4,
      ),
      decoration: BoxDecoration(
        color: outlined ? null : tone.surface(context),
        border: outlined ? Border.all(color: fg) : null,
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: fg),
            const SizedBox(width: AppSpacing.xs + 1),
          ],
          // Flexible, so a long label wraps inside the pill instead of
          // pushing it past the edge of the screen. MainAxisSize.min keeps a
          // short label's pill tight to its text, as before; this only takes
          // effect once there is not enough room, which on a compact phone
          // is what the method badge runs into.
          Flexible(
            child: Text(
              label,
              style: context.text.labelSmall?.copyWith(
                color: fg,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A ticker, set as an identifier rather than as prose.
class TickerChip extends StatelessWidget {
  const TickerChip(this.ticker, {super.key});

  final String ticker;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 3),
    decoration: BoxDecoration(
      color: context.scheme.surfaceContainerHighest,
      borderRadius: BorderRadius.circular(6),
    ),
    child: Text(
      ticker,
      style: context.text.labelSmall?.copyWith(
        color: context.scheme.onSurface,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.6,
      ),
    ),
  );
}

/// A labelled figure in a row of figures.
class LabelledValue extends StatelessWidget {
  const LabelledValue({
    super.key,
    required this.label,
    required this.value,
    this.valueKey,
    this.emphasis = false,
  });

  final String label;
  final String value;
  final Key? valueKey;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: context.text.labelSmall),
        const SizedBox(height: 2),
        Text(
          value,
          key: valueKey,
          style: emphasis ? context.text.titleLarge : context.text.titleSmall,
        ),
      ],
    );
  }
}

String _asPercent(double fraction) => '${(fraction * 100).toStringAsFixed(1)}%';

/// Return on invested capital, beside the cost of that capital.
///
/// The question this answers is not what the company is worth - the figure
/// above it already attempts that - but whether the business is worth owning
/// at all: does it earn more on the capital tied up in it than that capital
/// costs? A company can be cheap and value-destroying, or dear and excellent,
/// and the two readings disagreeing is information rather than a fault.
///
/// So the verdict leads and the numbers follow. Someone glancing reads one
/// line; someone checking reads the two percentages under it.
///
/// Three verdicts, not two. A gap inside a couple of percentage points is one
/// that figures drawn from a balance sheet cannot resolve, and a regulated
/// utility sitting a point below its cost of capital is doing roughly what
/// its regulator allows - not destroying value. "About" says that, where a
/// forced above-or-below would say something stronger and probably wrong.
class CapitalReturnsLine extends StatelessWidget {
  const CapitalReturnsLine({super.key, required this.returns});

  final CapitalReturns returns;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final (
      Color tone,
      IconData icon,
      String heading,
    ) = switch (returns.verdict) {
      CapitalVerdict.above => (
        colors.positive,
        Icons.trending_up_rounded,
        'Earns above its cost of capital',
      ),
      CapitalVerdict.below => (
        colors.negative,
        Icons.trending_down_rounded,
        'Earns below its cost of capital',
      ),
      CapitalVerdict.about => (
        colors.textSecondary,
        Icons.trending_flat_rounded,
        'Earns about its cost of capital',
      ),
    };

    return Container(
      key: const Key('capital-returns'),
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: context.scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 17, color: tone),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(
                  heading,
                  key: const Key('capital-returns-verdict'),
                  style: context.text.titleSmall?.copyWith(color: tone),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Padding(
            padding: const EdgeInsets.only(left: 17 + AppSpacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // The two figures, in the order the sentence reads them.
                Text(
                  'Return on invested capital '
                  '${_asPercent(returns.roic)} '
                  'vs cost of capital ${_asPercent(returns.wacc)}',
                  key: const Key('capital-returns-figures'),
                  style: context.text.bodySmall?.copyWith(
                    color: colors.textSecondary,
                  ),
                ),
                if (returns.readsAs.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    // The backend's own wording, shown as written.
                    'This business ${returns.readsAs}. '
                    'A historical measure of the business, not part of the '
                    'valuation above.',
                    style: context.text.bodySmall?.copyWith(
                      color: colors.textSecondary,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Plays its child in once, with a short rise and fade.
///
/// Used where a result replaces the waiting state. The point is to soften a
/// hard cut, not to stage an arrival: the layout is final on the first frame
/// and only opacity and a 1.5% translation animate, so the figure is legible
/// almost immediately and never moves far enough to be read as moving.
///
/// Keyed by the thing that arrived rather than rebuilt on every frame: a
/// slider drag rebuilds this subtree constantly, and an entrance that
/// restarted on each rebuild would make the whole screen shiver while the
/// user drags.
///
/// Opacity over a large subtree costs a saveLayer on every frame it runs, so
/// this is deliberately one-shot and short. Flutter skips the layer entirely
/// once opacity reaches 1, which means the cost ends when the animation does
/// rather than lasting as long as the screen.
class EntranceTransition extends StatefulWidget {
  const EntranceTransition({super.key, required this.child});

  final Widget child;

  @override
  State<EntranceTransition> createState() => _EntranceTransitionState();
}

class _EntranceTransitionState extends State<EntranceTransition>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: AppMotion.arrive,
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_controller.isDismissed) {
      _controller.duration = AppMotion.forContext(context, AppMotion.arrive);
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final curved = CurvedAnimation(
      parent: _controller,
      curve: AppMotion.enterCurve,
    );
    // The fade runs on its own, shorter clock than the travel, and starts
    // part-lit rather than at nothing. Both exist for the same reason: at
    // half a second, a figure that fades up from zero over the whole
    // duration is a figure the user cannot read while they are waiting to
    // read it. The answer is legible within the first few frames; what takes
    // the full duration is only its settling into place.
    final fade = CurvedAnimation(
      parent: _controller,
      curve: Interval(
        0,
        AppMotion.arriveFadeFraction,
        curve: AppMotion.enterCurve,
      ),
    );
    // The travel is dropped under reduced motion; the fade is kept, because
    // it is what says the content changed.
    final travel = AppMotion.reduced(context) ? 0.0 : AppMotion.enterTravel;
    return FadeTransition(
      opacity: Tween<double>(
        begin: AppMotion.arriveFadeFrom,
        end: 1.0,
      ).animate(fade),
      // Translate by a fixed distance rather than a fraction of the subtree.
      // The subtree is a short refusal card on one run and a full DCF on the
      // next, and a percentage of that made the same animation conspicuous
      // on one and invisible on the other.
      child: AnimatedBuilder(
        animation: curved,
        builder: (context, child) => Transform.translate(
          offset: Offset(0, travel * (1 - curved.value)),
          child: child,
        ),
        child: widget.child,
      ),
    );
  }
}

/// The shape of a result, drawn flat while one is being fetched.
///
/// This is the whole loading state, not a decoration above one. An earlier
/// version kept the old spinner block underneath and the screen read as two
/// things at once - a half-drawn result on top, a separate "loading" panel
/// below. One state, or it is not a state.
///
/// The blocks sit where the figure, the price-and-upside row and the
/// assumption rows will be, so the wait reads as the answer loading rather
/// than as the app having stopped. On a cold start that wait can run to a
/// minute, which is long enough for the difference to matter.
///
/// Deliberately static - no shimmer, nothing looping. A sweep would repaint
/// for that whole minute, on the weakest surface, at the moment the device
/// is already busy fetching, and it would tell the user nothing the still
/// shapes do not.
class ResultSkeleton extends StatelessWidget {
  const ResultSkeleton({
    super.key,
    required this.caption,
    this.detail,
    this.notice,
  });

  /// What is happening, in the words the screen already used.
  final String caption;
  final String? detail;

  /// Why this one is taking so long, once it has been long enough that the
  /// question has occurred to the user.
  ///
  /// Null for the first few seconds and on every quick valuation after,
  /// because a warning about a minute-long wait attached to a two-second
  /// one teaches the user to expect a slowness that is not there.
  final String? notice;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    // Faint enough to read as absence rather than as content. A placeholder
    // with presence competes with the figure that replaces it.
    final fill = colors.hairline;

    Widget block(double width, double height, {double radius = 6}) => Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: fill,
        borderRadius: BorderRadius.circular(radius),
      ),
    );

    Widget card({required List<Widget> children}) => Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: context.scheme.surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: children,
      ),
    );

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.xl,
        0,
        AppSpacing.xl,
        AppSpacing.xxxl,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // What is happening, said once, at the top where the company name
          // will be. The message belongs to this state rather than sitting
          // under it as a second panel.
          Text(caption, style: context.text.titleMedium),
          if (detail != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              detail!,
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
          // Appears only once the wait has actually run long. It fades in
          // rather than snapping, because something appearing instantly on a
          // screen the user has been staring at for four seconds reads as an
          // error arriving. Opacity on one small row: no layer worth the
          // name, unlike a fade over the skeleton itself.
          AnimatedSize(
            duration: AppMotion.forContext(context, AppMotion.enter),
            curve: AppMotion.panelCurve,
            alignment: Alignment.topLeft,
            child: notice == null
                ? const SizedBox(width: double.infinity)
                : Padding(
                    padding: const EdgeInsets.only(top: AppSpacing.lg),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.schedule_rounded,
                          size: 16,
                          color: colors.textSecondary,
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Expanded(
                          child: Text(
                            notice!,
                            style: context.text.bodySmall?.copyWith(
                              color: colors.textSecondary,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
          ),
          const SizedBox(height: AppSpacing.xl),

          // The figure, and the price/upside row beneath it.
          card(
            children: [
              block(120, 10),
              const SizedBox(height: AppSpacing.md),
              block(200, 38, radius: 8),
              const SizedBox(height: AppSpacing.xl),
              Divider(color: colors.hairline, height: 1),
              const SizedBox(height: AppSpacing.xl),
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        block(80, 9),
                        const SizedBox(height: AppSpacing.sm),
                        block(70, 18),
                      ],
                    ),
                  ),
                  const SizedBox(width: AppSpacing.xl),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        block(90, 9),
                        const SizedBox(height: AppSpacing.sm),
                        block(80, 18),
                      ],
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.xl),

          // Where the explanation note will be.
          card(
            children: [
              block(double.infinity, 9),
              const SizedBox(height: AppSpacing.sm),
              block(double.infinity, 9),
              const SizedBox(height: AppSpacing.sm),
              block(220, 9),
            ],
          ),
          const SizedBox(height: AppSpacing.xl),

          // Where the assumptions will be.
          for (var i = 0; i < 4; i++) ...[
            card(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [block(130, 14), block(60, 14)],
                ),
                const SizedBox(height: AppSpacing.md),
                block(double.infinity, 8),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
          ],
        ],
      ),
    );
  }
}

/// Shrinks very slightly while a finger is on it.
///
/// The feedback starts on the press, not on the release. A control that only
/// responds when the finger lifts reads as laggy even when nothing is slow,
/// because the response arrives after the user has finished acting. Native
/// controls answer the moment contact is made, and this is what that costs.
///
/// Subtle on purpose - 4% - and fast. The control should look like it took
/// the press, not like it performed. Anything larger starts drawing the eye
/// away from the figures, which on this screen is the thing motion is least
/// allowed to do.
///
/// Built on [Listener] rather than a gesture detector, and that is the whole
/// trick: a Listener watches raw pointer events without entering the gesture
/// arena, so the button inside keeps its own tap handling and its own Material
/// ripple. Wrapping a button in a GestureDetector instead puts two tap
/// recognisers in one arena, and whichever wins, the other's behaviour is
/// silently lost.
///
/// Cancelling matters as much as pressing: a finger that slides off has to
/// spring the control back, or it is left looking held down by a touch that
/// went nowhere.
///
/// Transform only, on a leaf widget. No opacity layer, so nothing here costs
/// a saveLayer.
class PressFeedback extends StatefulWidget {
  const PressFeedback({
    super.key,
    required this.child,
    this.enabled = true,
    this.scale = AppMotion.pressScale,
  });

  final Widget child;

  /// False leaves the control still: no feedback for a press that will not
  /// do anything.
  final bool enabled;

  final double scale;

  @override
  State<PressFeedback> createState() => _PressFeedbackState();
}

class _PressFeedbackState extends State<PressFeedback>
    with SingleTickerProviderStateMixin {
  /// Unbounded because a spring overshoots: a bounded controller would clamp
  /// the overshoot away and take the life out of the return.
  late final AnimationController _controller = AnimationController.unbounded(
    value: 1,
    vsync: this,
  );

  void _set(bool down) {
    if (!widget.enabled) return;
    // Reduced motion keeps the control responsive without moving it.
    if (AppMotion.reduced(context)) {
      _controller.stop();
      _controller.value = 1;
      return;
    }
    // Starting from the current value *and the current velocity* is the
    // whole of interruptibility: press again while it is springing back and
    // it carries that motion into the new target instead of snapping to
    // where it would have been and starting over.
    _controller.animateWith(
      SpringSimulation(
        down ? AppMotion.pressDown : AppMotion.pressRelease,
        _controller.value,
        down ? widget.scale : 1.0,
        _controller.velocity,
      ),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      // Translucent rather than the default deferToChild: the squeeze should
      // answer a press anywhere in the control's bounds, including any
      // padding around the child, and a child that does not take part in hit
      // testing should not silently disable the feedback. Translucent adds
      // this to the hit path without taking anything away from what is
      // behind it, which matters because a Listener is purely observational.
      behavior: HitTestBehavior.translucent,
      onPointerDown: widget.enabled ? (_) => _set(true) : null,
      onPointerUp: widget.enabled ? (_) => _set(false) : null,
      onPointerCancel: widget.enabled ? (_) => _set(false) : null,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) =>
            Transform.scale(scale: _controller.value, child: child),
        child: widget.child,
      ),
    );
  }
}

/// Slides one lens out while the other slides in.
///
/// The two lenses are a pair of views onto the same company, and a cut
/// between them says nothing about which way the user went. Moving them
/// horizontally does: the relative view lives to the right of the intrinsic
/// one, and the content travels the same way the tab indicator does.
///
/// The two views move as one rigid strip, and that is the whole of this
/// widget. Their offsets are read from a single animation and always differ
/// by exactly one viewport width, so at every frame the pair tiles the
/// viewport: one of them covers the part the other has vacated, and neither
/// ever covers the other.
///
/// This was first built on AnimatedSwitcher, which cannot do that. It gives
/// each view its own controller and its own curve, and under a decelerate
/// curve the arriving view rushes to the centre - 84% of the way there by a
/// third of the duration - while the leaving view has barely started, 0.5%
/// of the way out. For most of the transition both sat in the middle of the
/// screen drawing over each other: the gap between them, which has to stay
/// at 1.0 for the strip to hold together, fell to 0.068 at the half-way
/// point. No amount of clipping addresses that, because the overlap is not
/// outside the viewport - it is the whole of the viewport.
///
/// Transform only. No opacity anywhere, deliberately: this is the largest
/// subtree in the app - a full set of sliders, a sensitivity grid, a peer
/// table - and an opacity layer over it would mean a saveLayer over the lot,
/// every frame, on the surface least able to afford one. A fractional
/// translation costs a transform on an existing layer and nothing else.
///
/// The clip is a plain rectangle for the same reason: it bounds the halves
/// of each view that have travelled past the edge without allocating
/// anything.
class LensTransition extends StatefulWidget {
  const LensTransition({super.key, required this.child});

  /// Keyed by which lens it is: that key is what tells one view from
  /// another rather than the same one rebuilding, which this subtree does
  /// on every frame of a slider drag.
  final Widget child;

  /// The keys the two views must carry.
  static const Key intrinsicKey = ValueKey('lens-intrinsic');
  static const Key relativeKey = ValueKey('lens-relative');

  @override
  State<LensTransition> createState() => _LensTransitionState();
}

class _LensTransitionState extends State<LensTransition>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller =
      AnimationController(
        vsync: this,
        duration: AppMotion.lens,
      )..addStatusListener((status) {
        // The view that has left is unmounted the moment it is off screen.
        // Leaving it in place would keep the heaviest subtree in the app built
        // and painting behind the one the user is reading.
        if (status == AnimationStatus.completed && _leaving != null) {
          setState(() => _leaving = null);
        }
      });

  Widget? _leaving;

  /// +1 when the arriving view comes from the right.
  double _direction = 1;

  @override
  void didUpdateWidget(LensTransition old) {
    super.didUpdateWidget(old);
    if (old.child.key == widget.child.key) return;
    _leaving = old.child;
    _direction = widget.child.key == LensTransition.relativeKey ? 1 : -1;
    _controller.forward(from: 0);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final leaving = _leaving;
    // An instant switch for anyone who asked for less motion, and nothing
    // built around it: the two views are never mounted at once.
    if (leaving == null || AppMotion.reduced(context)) {
      return KeyedSubtree(key: widget.child.key, child: widget.child);
    }

    final progress = CurvedAnimation(
      parent: _controller,
      curve: AppMotion.enterCurve,
    );
    // One animation, two offsets, a constant width apart. The arriving view
    // closes on the centre from `_direction`; the leaving view is wherever
    // the arriving one is, less that same full width.
    final arriving = Tween<Offset>(
      begin: Offset(_direction, 0),
      end: Offset.zero,
    ).animate(progress);
    final departing = Tween<Offset>(
      begin: Offset.zero,
      end: Offset(-_direction, 0),
    ).animate(progress);

    return ClipRect(
      child: Stack(
        alignment: Alignment.topCenter,
        children: [
          // Positioned, so the view on its way out takes no part in sizing
          // the pair. Without this the page would stand as tall as the
          // taller lens for the length of the transition and change height
          // twice on the way through.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: SlideTransition(position: departing, child: leaving),
          ),
          SlideTransition(
            position: arriving,
            child: KeyedSubtree(key: widget.child.key, child: widget.child),
          ),
        ],
      ),
    );
  }
}
