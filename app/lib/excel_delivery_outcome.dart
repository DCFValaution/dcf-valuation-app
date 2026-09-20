/// What happened when a workbook was handed to the platform.
///
/// The distinction that matters is between a failure and an *instruction*:
/// an iPhone home-screen app can genuinely be unable to save a file, and the
/// honest answer there is a route that works ("open it in Safari"), not a red
/// error the user can only stare at.
sealed class DeliveryOutcome {
  const DeliveryOutcome();
}

/// The file reached the user - saved, downloaded, or handed to a share sheet.
class DeliveryDone extends DeliveryOutcome {
  /// Shown to the user, or null when the platform's own UI already said so
  /// (a share sheet, or the browser's download shelf).
  final String? message;
  const DeliveryDone([this.message]);
}

/// The user backed out of the share sheet. Nothing went wrong, so nothing is
/// reported.
class DeliveryCancelled extends DeliveryOutcome {
  const DeliveryCancelled();
}

/// This context cannot save a file, but another one can. The message tells the
/// user exactly what to do, and is shown as guidance rather than as an error.
class DeliveryInstruction extends DeliveryOutcome {
  final String message;
  const DeliveryInstruction(this.message);
}

/// Something actually failed.
class DeliveryFailure extends DeliveryOutcome {
  final String message;
  const DeliveryFailure(this.message);
}
