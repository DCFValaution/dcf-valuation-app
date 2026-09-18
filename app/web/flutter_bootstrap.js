{{flutter_js}}
{{flutter_build_config}}

// Loads the app WITHOUT Flutter's service worker.
//
// That worker is deprecated in this Flutter release: the script it installs
// does nothing but unregister itself on activation. Registering it still costs
// something, though - the loader waits for it to activate (up to four seconds)
// before it starts downloading the app, and on a phone that wait lands on
// every cold start while the user looks at the splash. Nothing is cached by
// it, so dropping it loses nothing.
_flutter.loader.load();
