#!/usr/bin/env bash
#
# Builds the Flutter web app on Render's static-site builder.
#
# Render's build image has no Flutter SDK, so this fetches one. The version is
# pinned to the version the app is developed against: an unpinned "stable"
# would silently change under the app on some future deploy, and a Flutter
# upgrade should be a commit that can be reviewed and reverted, not a surprise
# from a rebuild of unchanged code.
#
# The SDK lands in the build container and is thrown away with it. Render
# caches nothing between builds here, so a deploy takes a few minutes.

set -euo pipefail

FLUTTER_VERSION="3.47.2"
FLUTTER_DIR="${HOME}/flutter"

if [ ! -d "${FLUTTER_DIR}" ]; then
  echo "Fetching Flutter ${FLUTTER_VERSION}"
  git clone --depth 1 --branch "${FLUTTER_VERSION}" \
    https://github.com/flutter/flutter.git "${FLUTTER_DIR}"
fi

export PATH="${FLUTTER_DIR}/bin:${PATH}"

# Render's builder runs as a different user from the one that cloned the SDK
# in some images; without this git refuses to read it.
git config --global --add safe.directory "${FLUTTER_DIR}" || true

flutter --version
flutter config --enable-web

cd app
flutter pub get

# No --dart-define: the app's compiled-in default is the deployed API.
flutter build web --release

echo "Built app/build/web"
