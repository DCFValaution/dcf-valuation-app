"""Serve the built web app the way Render serves it.

WHY THIS EXISTS
---------------
`flutter run -d chrome` serves through its own development server, which
sends neither the headers production sends nor, in particular, a
Cross-Origin-Opener-Policy. So a sign-in problem that only appears under
a particular COOP cannot be reproduced there, and a fix cannot be
confirmed there either.

This serves `app/build/web` with the headers from `render.yaml`, so what
you test locally is what the static site will do. It is a test rig, not a
server: single-threaded, no caching, no TLS.

    python tools/serve_web.py            # http://localhost:8090
    python tools/serve_web.py --port 9000
    python tools/serve_web.py --coop same-origin    # reproduce the break

Build first:

    cd app
    flutter build web --release --dart-define=BACKEND_BASE_URL=http://localhost:8000
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "app", "build", "web")

# The headers render.yaml sets, minus the ones only a CDN can honour.
#
# Cross-Origin-Opener-Policy is the one that matters here: Firebase's
# sign-in pop-up has to be able to report back to the page that opened
# it, and `same-origin` severs that link.
HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    """Adds the production headers, and serves a single-page app."""

    coop = HEADERS["Cross-Origin-Opener-Policy"]

    def end_headers(self) -> None:
        for name, value in HEADERS.items():
            if name == "Cross-Origin-Opener-Policy":
                value = self.coop
            self.send_header(name, value)
        # Never let a browser pin an old build during testing.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        # Every unknown path is index.html, the same rewrite render.yaml
        # does - or a reload on /delete would 404.
        path = self.translate_path(self.path)
        if not os.path.exists(path) and not self.path.startswith("/_"):
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--coop", default="same-origin-allow-popups",
        help="Cross-Origin-Opener-Policy to send. Pass `same-origin` to "
             "reproduce a pop-up sign-in failing, or `none` to omit it.")
    args = parser.parse_args()

    root = os.path.normpath(ROOT)
    if not os.path.isdir(root):
        sys.exit(f"No build at {root}.\nBuild it first:\n"
                 "  cd app\n"
                 "  flutter build web --release "
                 "--dart-define=BACKEND_BASE_URL=http://localhost:8000")

    if args.coop.lower() == "none":
        HEADERS.pop("Cross-Origin-Opener-Policy", None)
    else:
        Handler.coop = args.coop

    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=root)
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving {root}")
        print(f"  http://localhost:{args.port}")
        print(f"  Cross-Origin-Opener-Policy: "
              f"{HEADERS.get('Cross-Origin-Opener-Policy', '(not sent)')}")
        print("Ctrl-C to stop.")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
