/// How a finished workbook reaches the user, which differs by platform.
///
/// On Android and iOS the bytes are written to the app's own documents
/// directory and handed to the system share sheet. On the web there is no
/// filesystem: the bytes have to become a download or a Web Share payload in
/// the browser itself, and which of those works depends on the browser - most
/// sharply on an iPhone, where a home-screen (standalone) app blocks the
/// ordinary anchor download that works everywhere else.
library;

export 'excel_delivery_io.dart'
    if (dart.library.js_interop) 'excel_delivery_web.dart';
export 'excel_delivery_outcome.dart';
