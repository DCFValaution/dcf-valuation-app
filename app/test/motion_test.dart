/// The three animations, and the promises they have to keep.
///
/// This app's identity is clarity about numbers, so motion here is held to a
/// stricter bar than usual: it may soften a change, and it may not delay,
/// obscure, or redraw a figure. These tests are the parts of that which can
/// be checked without a device.
///
/// What cannot be checked here is feel - whether 200ms reads as crisp or
/// slow, and whether the rise is small enough not to register as movement.
/// That needs eyes on hardware.
library;

import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dcf_app/advanced_panel.dart';
import 'package:dcf_app/dcf_engine.dart' show fadeLinear;
import 'package:dcf_app/theme.dart';
import 'package:dcf_app/ui.dart';

Widget wrap(Widget child, {bool reducedMotion = false}) => MaterialApp(
  theme: buildLightTheme(),
  // Inside the app, not around it: MaterialApp builds its own MediaQuery
  // from the window and would discard an outer one.
  builder: (context, _) => MediaQuery(
    data: MediaQuery.of(context).copyWith(disableAnimations: reducedMotion),
    child: Scaffold(body: child),
  ),
  home: const SizedBox.shrink(),
);

/// The app's own route machinery contains FadeTransitions of its own, so
/// every assertion about ours is scoped to the widget under test.
Finder fadeIn(Type owner) => find.descendant(
  of: find.byType(owner),
  matching: find.byType(FadeTransition),
);

Finder slideIn(Type owner) => find.descendant(
  of: find.byType(owner),
  matching: find.byType(SlideTransition),
);

/// The travel a widget's own Transform is currently applying.
Offset translationOf(WidgetTester tester, Type owner) {
  final t = tester
      .widgetList<Transform>(
        find.descendant(
          of: find.byType(owner),
          matching: find.byType(Transform),
        ),
      )
      .first;
  final v = t.transform.getTranslation();
  return Offset(v.x, v.y);
}

/// The scale a widget's own Transform is currently applying.
///
/// Read off the x axis rather than with getMaxScaleOnAxis, which takes the
/// largest of the three and so reports the untouched z axis - a constant 1.0
/// - for every scale below 1.
double scaleOf(WidgetTester tester, Type owner) => tester
    .widgetList<Transform>(
      find.descendant(of: find.byType(owner), matching: find.byType(Transform)),
    )
    .first
    .transform
    .entry(0, 0);

void main() {
  group('the result arrives without being delayed', () {
    testWidgets('its content is laid out on the very first frame', (
      tester,
    ) async {
      // The whole point of animating opacity and a translation rather than
      // anything structural: the figure is there immediately, and only its
      // presentation settles.
      await tester.pumpWidget(
        wrap(const EntranceTransition(child: Text(r'$117.98'))),
      );
      await tester.pump(Duration.zero);

      expect(find.text(r'$117.98'), findsOneWidget);
      expect(tester.getSize(find.text(r'$117.98')).height, greaterThan(0));
    });

    testWidgets('it settles within the stated duration', (tester) async {
      await tester.pumpWidget(
        wrap(const EntranceTransition(child: Text('result'))),
      );
      await tester.pump(Duration.zero);

      final fade = tester.widget<FadeTransition>(
        fadeIn(EntranceTransition).first,
      );
      expect(
        fade.opacity.value,
        lessThan(1.0),
        reason: 'it should start faded',
      );

      await tester.pump(AppMotion.arrive);
      expect(
        tester
            .widget<FadeTransition>(fadeIn(EntranceTransition).first)
            .opacity
            .value,
        1.0,
        reason: 'and be finished by AppMotion.arrive, not lingering past it',
      );
    });

    testWidgets('the travel is small enough not to read as movement', (
      tester,
    ) async {
      // Far enough to be seen as movement rather than a bare fade, and
      // short enough that the figure is not travelling across the screen.
      expect(AppMotion.enterTravel, inInclusiveRange(16.0, 48.0));
      // This one is allowed to run long - it is the only motion in the app
      // that is meant to be noticed - but only because it withholds nothing
      // while it runs. That is what the next two tests are for.
      expect(AppMotion.arrive.inMilliseconds, lessThanOrEqualTo(550));
    });

    testWidgets('the figure is readable on the very first frame', (
      tester,
    ) async {
      // The constraint that makes a 500ms entrance acceptable. A fade from
      // nothing would mean the number the user asked for is genuinely
      // absent while they are waiting to read it; this one starts part-lit
      // and the motion only settles a figure that is already there.
      await tester.pumpWidget(
        wrap(const EntranceTransition(child: Text(r'$117.98'))),
      );
      await tester.pump(Duration.zero);

      final opacity = tester
          .widget<FadeTransition>(fadeIn(EntranceTransition).first)
          .opacity
          .value;
      expect(opacity, greaterThanOrEqualTo(AppMotion.arriveFadeFrom - 0.01));
      expect(opacity, lessThan(1.0), reason: 'it should still be arriving');
    });

    testWidgets('the fade finishes well before the travel does', (
      tester,
    ) async {
      await tester.pumpWidget(
        wrap(const EntranceTransition(child: Text('result'))),
      );
      await tester.pump(Duration.zero);

      // By the time the slide is half way, the content is fully lit.
      await tester.pump(AppMotion.arrive ~/ 2);
      expect(
        tester
            .widget<FadeTransition>(fadeIn(EntranceTransition).first)
            .opacity
            .value,
        1.0,
      );
      // ...while it is still travelling.
      expect(translationOf(tester, EntranceTransition).dy, greaterThan(0));
    });

    testWidgets('it plays once, not on every rebuild', (tester) async {
      // A slider drag rebuilds this subtree continuously. An entrance that
      // restarted each time would make the screen shiver under the finger.
      await tester.pumpWidget(
        wrap(
          const EntranceTransition(
            key: ValueKey('AAPL'),
            child: Text('result'),
          ),
        ),
      );
      await tester.pump(AppMotion.arrive);
      expect(
        tester
            .widget<FadeTransition>(fadeIn(EntranceTransition).first)
            .opacity
            .value,
        1.0,
      );

      // Same key, new build: no restart.
      await tester.pumpWidget(
        wrap(
          const EntranceTransition(
            key: ValueKey('AAPL'),
            child: Text('result changed'),
          ),
        ),
      );
      await tester.pump(Duration.zero);
      expect(
        tester
            .widget<FadeTransition>(fadeIn(EntranceTransition).first)
            .opacity
            .value,
        1.0,
        reason: 'a rebuild must not replay the entrance',
      );
    });
  });

  group('reduced motion is honoured', () {
    testWidgets('the entrance is effectively instant', (tester) async {
      await tester.pumpWidget(
        wrap(
          const EntranceTransition(child: Text('result')),
          reducedMotion: true,
        ),
      );
      await tester.pump(const Duration(milliseconds: 2));

      expect(
        tester
            .widget<FadeTransition>(fadeIn(EntranceTransition).first)
            .opacity
            .value,
        1.0,
        reason: 'someone who asked for less motion should not wait for it',
      );
      expect(find.text('result'), findsOneWidget);
    });

    testWidgets('the travel is dropped but the fade is kept', (tester) async {
      // Gentler, not absent: the fade still says the content changed.
      await tester.pumpWidget(
        wrap(
          const EntranceTransition(child: Text('result')),
          reducedMotion: true,
        ),
      );
      await tester.pump(Duration.zero);

      expect(
        translationOf(tester, EntranceTransition),
        Offset.zero,
        reason: 'no travel under reduced motion',
      );
    });
  });

  group('the Advanced panel reveals by size, not by fading a subtree', () {
    Widget panel({required bool expanded}) => wrap(
      SingleChildScrollView(
        child: AdvancedPanel(
          expanded: expanded,
          onExpandedChanged: (_) {},
          fadeEnabled: false,
          onFadeChanged: (_) {},
          fadeStartYear: 3,
          onStartYearChanged: (_) {},
          fadePattern: fadeLinear,
          onPatternChanged: (_) {},
          projectionYears: 5,
          growthPath: const [],
        ),
      ),
    );

    testWidgets('it animates its own size', (tester) async {
      await tester.pumpWidget(panel(expanded: false));
      expect(find.byType(AnimatedSize), findsOneWidget);
    });

    testWidgets('no Opacity layer is drawn over the contents', (tester) async {
      // An opacity layer over this subtree would cost a saveLayer on every
      // frame it ran, which the web build is the surface least able to
      // afford. The size animates; the contents do not fade.
      await tester.pumpWidget(panel(expanded: true));
      await tester.pump(AppMotion.enter);

      final opacities = find.descendant(
        of: find.byType(AdvancedPanel),
        matching: find.byType(FadeTransition),
      );
      expect(opacities, findsNothing);
    });

    testWidgets('no square corner appears behind the card while it opens', (
      tester,
    ) async {
      // The bug this guards, found on a device: AppCard paints a rounded
      // background but is a plain Container, so the header's ink landed on
      // whatever Material sat behind the card. Under the opaque white it was
      // invisible - except in the four corners, where it escaped the curve
      // and drew grey squares for as long as the tap lasted, which is
      // exactly as long as the section takes to open.
      //
      // Only pixels can see this, so this test reads them.
      const page = Color(0xFFF4F6FA);
      Widget page_({required bool expanded}) => MaterialApp(
        theme: buildLightTheme(),
        home: Scaffold(
          backgroundColor: page,
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: SingleChildScrollView(
              child: RepaintBoundary(
                child: AdvancedPanel(
                  expanded: expanded,
                  onExpandedChanged: (_) {},
                  fadeEnabled: false,
                  onFadeChanged: (_) {},
                  fadeStartYear: 3,
                  onStartYearChanged: (_) {},
                  fadePattern: fadeLinear,
                  onPatternChanged: (_) {},
                  projectionYears: 5,
                  growthPath: const [],
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpWidget(page_(expanded: false));

      // Hold the header down, then open the section: the ripple is in
      // flight for exactly as long as the expansion.
      await tester.startGesture(
        tester.getCenter(find.byKey(const Key('advanced-header'))),
      );
      await tester.pump(const Duration(milliseconds: 60));
      await tester.pumpWidget(page_(expanded: true));
      await tester.pump(AppMotion.enter ~/ 2);

      final boundary = tester.renderObject<RenderRepaintBoundary>(
        find.byType(RepaintBoundary).first,
      );
      // Rasterising goes through the engine, which only runs real async work
      // outside the fake-async zone a widget test lives in.
      late final int width;
      late final Uint8List pixels;
      await tester.runAsync(() async {
        final image = await boundary.toImage();
        final data = await image.toByteData(format: ui.ImageByteFormat.rawRgba);
        pixels = data!.buffer.asUint8List();
        width = image.width;
      });

      Color at(int x, int y) {
        final i = (y * width + x) * 4;
        return Color.fromARGB(
          pixels[i + 3],
          pixels[i],
          pixels[i + 1],
          pixels[i + 2],
        );
      }

      // Just inside the card's bounding box but outside its rounded curve -
      // where the stray ink used to show. The card sits at (16, 16).
      // Just inside the card's bounding box but outside its rounded curve -
      // where the stray ink showed. Without the Material's radius this reads
      // (230, 231, 234); the card sits at (16, 16).
      expect(
        at(20, 20),
        page,
        reason: 'the corner outside the curve must be the page, not grey ink',
      );
      // And the ripple is now actually visible on the card, which it never
      // was while it was painting underneath an opaque background.
      expect(at(30, 30), isNot(const Color(0xFFFFFFFF)));
    });

    testWidgets('collapsed, it takes no height', (tester) async {
      await tester.pumpWidget(panel(expanded: false));
      await tester.pump(AppMotion.enter);
      final collapsed = tester.getSize(find.byType(AdvancedPanel)).height;

      await tester.pumpWidget(panel(expanded: true));
      await tester.pump(AppMotion.enter);
      final open = tester.getSize(find.byType(AdvancedPanel)).height;

      expect(open, greaterThan(collapsed));
    });
  });

  group('press feedback answers the finger, not the release', () {
    testWidgets('it shrinks on press down and springs back on release', (
      tester,
    ) async {
      var taps = 0;
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(
              // Opaque because a bare SizedBox takes no part in hit
              // testing; a real button does, which is why this only has to
              // be said in the test.
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => taps++,
                child: const SizedBox(width: 120, height: 48),
              ),
            ),
          ),
        ),
      );

      expect(scaleOf(tester, PressFeedback), 1.0);

      final gesture = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      // A spring does not teleport to its target, so the promise is that it
      // is already moving on the next frame - not that it has arrived.
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 16));
      expect(
        scaleOf(tester, PressFeedback),
        lessThan(1.0),
        reason: 'the response must start on contact, not on release',
      );

      // Held, it settles at the pressed size.
      await tester.pump(const Duration(milliseconds: 400));
      expect(
        scaleOf(tester, PressFeedback),
        closeTo(AppMotion.pressScale, 0.005),
      );

      await gesture.up();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(scaleOf(tester, PressFeedback), closeTo(1.0, 0.005));
      expect(taps, 1);
    });

    testWidgets('the return overshoots: it springs, not eases', (tester) async {
      // The difference between alive and mechanical. A curve would approach
      // 1.0 from below and stop there; a spring passes it and comes back.
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(child: const SizedBox(width: 120, height: 48)),
          ),
        ),
      );

      final gesture = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      await gesture.up();

      var peak = 0.0;
      for (var i = 0; i < 40; i++) {
        await tester.pump(const Duration(milliseconds: 8));
        final scale = scaleOf(tester, PressFeedback);
        if (scale > peak) peak = scale;
      }
      expect(
        peak,
        greaterThan(1.0),
        reason: 'the release should overshoot before settling',
      );
      expect(
        peak,
        lessThan(1.03),
        reason: 'but barely - this is a button, not a toy',
      );
    });

    testWidgets('a press part way through the return is not restarted', (
      tester,
    ) async {
      // Interruptibility, which is the reason this is a simulation at all:
      // the second press picks the control up where it is, so it never jumps.
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(child: const SizedBox(width: 120, height: 48)),
          ),
        ),
      );

      final first = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      await first.up();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 40));

      final midReturn = scaleOf(tester, PressFeedback);
      expect(midReturn, greaterThan(AppMotion.pressScale));
      expect(midReturn, lessThan(1.0), reason: 'caught mid-flight');

      await tester.startGesture(tester.getCenter(find.byType(PressFeedback)));
      await tester.pump();
      expect(
        scaleOf(tester, PressFeedback),
        closeTo(midReturn, 0.01),
        reason: 'the new press continues from here, it does not snap',
      );
    });

    testWidgets('a finger that slides off springs it back', (tester) async {
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(child: const SizedBox(width: 120, height: 48)),
          ),
        ),
      );

      final gesture = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 200));
      await gesture.moveBy(const Offset(0, 400));
      await gesture.up();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));

      expect(
        scaleOf(tester, PressFeedback),
        closeTo(1.0, 0.005),
        reason: 'a cancelled press must not leave the control held down',
      );
    });

    testWidgets('a disabled control gives no feedback', (tester) async {
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(
              enabled: false,
              child: SizedBox(width: 120, height: 48),
            ),
          ),
        ),
      );

      final gesture = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(
        scaleOf(tester, PressFeedback),
        1.0,
        reason: 'no feedback for a press that will not do anything',
      );
      await gesture.up();
    });

    testWidgets('reduced motion keeps it responsive without moving it', (
      tester,
    ) async {
      await tester.pumpWidget(
        wrap(
          Center(
            child: PressFeedback(child: const SizedBox(width: 120, height: 48)),
          ),
          reducedMotion: true,
        ),
      );

      final gesture = await tester.startGesture(
        tester.getCenter(find.byType(PressFeedback)),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(
        scaleOf(tester, PressFeedback),
        1.0,
        reason: 'someone who asked for less motion gets none of this spring',
      );
      await gesture.up();
    });
  });

  group('the lenses slide, and pay nothing for it', () {
    Widget lens(bool relative) => wrap(
      LensTransition(
        child: relative
            ? const KeyedSubtree(
                key: LensTransition.relativeKey,
                child: Text('peers'),
              )
            : const KeyedSubtree(
                key: LensTransition.intrinsicKey,
                child: Text('figure'),
              ),
      ),
    );

    testWidgets('no opacity layer is drawn over the content', (tester) async {
      // The whole point of sliding rather than cross-fading. This is the
      // largest subtree in the app, and an opacity layer over it would mean
      // a saveLayer over the lot on every frame of the transition, on the
      // surface least able to afford one.
      await tester.pumpWidget(lens(false));
      await tester.pumpWidget(lens(true));
      await tester.pump(AppMotion.lens ~/ 2);

      expect(fadeIn(LensTransition), findsNothing);
      expect(
        find.descendant(
          of: find.byType(LensTransition),
          matching: find.byType(Opacity),
        ),
        findsNothing,
      );
      expect(
        find.descendant(
          of: find.byType(LensTransition),
          matching: find.byType(AnimatedOpacity),
        ),
        findsNothing,
      );
    });

    testWidgets('the relative lens arrives from the right', (tester) async {
      await tester.pumpWidget(lens(false));
      await tester.pumpWidget(lens(true));
      await tester.pump(Duration.zero);

      final arriving = tester.widget<SlideTransition>(
        find
            .ancestor(
              of: find.text('peers'),
              matching: find.byType(SlideTransition),
            )
            .first,
      );
      expect(
        arriving.position.value.dx,
        greaterThan(0),
        reason: 'the right-hand tab comes in from the right',
      );
    });

    testWidgets('and leaves the way it came', (tester) async {
      // Which is what makes the direction read without anyone thinking
      // about it - and it falls out of the construction, since an exit
      // replays the entrance backwards.
      await tester.pumpWidget(lens(false));
      await tester.pumpWidget(lens(true));
      await tester.pump(AppMotion.lens + const Duration(milliseconds: 20));

      await tester.pumpWidget(lens(false));
      await tester.pump(const Duration(milliseconds: 40));

      final leaving = tester.widget<SlideTransition>(
        find
            .ancestor(
              of: find.text('peers'),
              matching: find.byType(SlideTransition),
            )
            .first,
      );
      expect(
        leaving.position.value.dx,
        greaterThan(0),
        reason: 'the relative lens goes back out to the right',
      );

      final arriving = tester.widget<SlideTransition>(
        find
            .ancestor(
              of: find.text('figure'),
              matching: find.byType(SlideTransition),
            )
            .first,
      );
      expect(
        arriving.position.value.dx,
        lessThan(0),
        reason: 'while the intrinsic one comes back in from the left',
      );
    });

    testWidgets('both are on screen only while it moves', (tester) async {
      await tester.pumpWidget(lens(false));
      await tester.pumpWidget(lens(true));
      await tester.pump(AppMotion.lens ~/ 2);
      expect(find.text('figure'), findsOneWidget);
      expect(find.text('peers'), findsOneWidget);

      await tester.pump(AppMotion.lens);
      expect(
        find.text('figure'),
        findsNothing,
        reason: 'the old lens must not be left mounted behind the new one',
      );
    });

    testWidgets('a rebuild of the same lens does not restart it', (
      tester,
    ) async {
      // This subtree rebuilds on every frame of a slider drag. If that read
      // as a new lens the screen would slide under the finger.
      await tester.pumpWidget(lens(true));
      await tester.pump(AppMotion.lens + const Duration(milliseconds: 20));
      await tester.pumpWidget(lens(true));
      await tester.pump(Duration.zero);

      expect(
        tester
            .widget<SlideTransition>(
              find
                  .ancestor(
                    of: find.text('peers'),
                    matching: find.byType(SlideTransition),
                  )
                  .first,
            )
            .position
            .value,
        Offset.zero,
      );
    });

    testWidgets('reduced motion switches instantly', (tester) async {
      await tester.pumpWidget(
        wrap(
          const LensTransition(
            child: KeyedSubtree(
              key: LensTransition.relativeKey,
              child: Text('peers'),
            ),
          ),
          reducedMotion: true,
        ),
      );
      await tester.pump(Duration.zero);

      expect(find.text('peers'), findsOneWidget);
      expect(
        find.descendant(
          of: find.byType(LensTransition),
          matching: find.byType(SlideTransition),
        ),
        findsNothing,
        reason: 'no transition at all for someone who asked for none',
      );
    });
  });

  group('the loading skeleton', () {
    testWidgets('keeps the wording the screen already used', (tester) async {
      await tester.pumpWidget(
        wrap(
          const ResultSkeleton(
            caption: 'Waking up the server',
            detail: 'This can take up to a minute on the first use.',
          ),
        ),
      );

      expect(find.text('Waking up the server'), findsOneWidget);
      expect(find.textContaining('up to a minute'), findsOneWidget);
    });

    testWidgets('it is static: nothing on it tweens', (tester) async {
      // A shimmer would repaint for the whole wait - up to a minute, on the
      // weakest surface, while the device is already busy fetching.
      await tester.pumpWidget(
        wrap(const ResultSkeleton(caption: 'Valuing AAPL')),
      );

      expect(fadeIn(ResultSkeleton), findsNothing);
      expect(slideIn(ResultSkeleton), findsNothing);
      expect(
        find.descendant(
          of: find.byType(ResultSkeleton),
          matching: find.byType(AnimatedOpacity),
        ),
        findsNothing,
      );
      expect(
        find.descendant(
          of: find.byType(ResultSkeleton),
          matching: find.byType(AnimatedContainer),
        ),
        findsNothing,
      );
    });

    testWidgets('it shows the shape of the answer, not an empty screen', (
      tester,
    ) async {
      await tester.pumpWidget(
        wrap(const ResultSkeleton(caption: 'Valuing AAPL')),
      );
      // Placeholder blocks standing in for the figure, the metric row and
      // the assumption rows.
      expect(find.byType(Container), findsAtLeastNWidgets(10));
    });
  });
}
