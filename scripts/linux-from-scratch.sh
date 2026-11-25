#!/bin/bash
# Build Wavefunction on a bare Ubuntu 24.04 by following README's Linux section word for word, then
# check what it built. .github/workflows/linux-from-scratch.yml runs this as root in
# `docker run --privileged ubuntu:24.04`, with this checkout at /source and /out for what it keeps.
# The steps themselves run as an ordinary user with sudo, as on a desktop.
#
#   linux-from-scratch.sh build     README's steps 1 to 3, the built engine's own checks, and two
#                                   AppImages for the update check. Keeps the packages in /out.
#   linux-from-scratch.sh desktop   README's step 4 on another bare system, with /build holding what
#                                   `build` kept, then scripts/verify-linux-desktop.py.
set -euo pipefail

stage=${1:?build or desktop}
user=tester

if [ "$(id -u)" = 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  # What a fresh Ubuntu desktop has before README's steps begin.
  apt-get install -y -q --no-install-recommends sudo ca-certificates wget python3 procps fuse3 xz-utils
  useradd -m -s /bin/bash "$user"
  echo "$user ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$user"
  # README's `sudo apt install` lines wait for a yes.
  echo 'APT::Get::Assume-Yes "true";' > /etc/apt/apt.conf.d/90assume-yes
  chown "$user" /out
  exec runuser -u "$user" -- env HOME="/home/$user" DEBIAN_FRONTEND=noninteractive "$0" "$stage"
fi

# The Nth ```sh block of README's Linux section, without the list's indent.
readme_block() {
  awk -v want="$1" '
    /^### Linux/ { linux = 1; next }
    linux && /^##/ { exit }
    linux && /^ *```sh/ { block++; inside = block == want; next }
    linux && /^ *```/ { inside = 0; next }
    inside { sub(/^   /, ""); print }
  ' /source/README.md
}

# README's fix for Ubuntu's AppArmor refusing bwrap its user namespaces.
allow_user_namespaces() {
  if [ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
  fi
}

build() {
  cd
  echo "::group::1. Node.js 22, uv and Rust, from the installers README links to"
  sudo apt-get install -y -q curl
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
  export NVM_DIR="$HOME/.nvm"
  set +u
  . "$NVM_DIR/nvm.sh" || true
  nvm install 22
  set -u
  curl -LsSf https://astral.sh/uv/install.sh | sh
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  . "$HOME/.cargo/env"
  export PATH="$HOME/.local/bin:$PATH"
  echo "::endgroup::"

  echo "::group::2. README's apt line"
  bash -euxo pipefail -c "$(readme_block 1)"
  echo "::endgroup::"

  echo "::group::3. README's clone and build, cloning this checkout instead of GitHub"
  git config --global url./source.insteadOf https://github.com/stoyan-stoyanov/wavefunction.git
  git config --global --add safe.directory '*'
  bash -euxo pipefail -c "$(readme_block 2)"
  echo "::endgroup::"

  cd ~/wavefunction
  ls -l dist/desktop
  cp dist/desktop/*.deb /out/

  echo "::group::The built engine: sandbox self-check and a scripted coding session"
  allow_user_namespaces
  backend/.venv/bin/python scripts/verify-coding-features.py \
    --backend backend/dist/wavefunction-backend/wavefunction-backend
  echo "::endgroup::"

  echo "::group::Two AppImages that trust a throwaway key, for the update check"
  local tauri=./frontend/node_modules/.bin/tauri key="$HOME/updater.key" version arch
  "$tauri" signer generate --ci --password "" --write-keys "$key"
  version=$(node -p 'require("./src-tauri/tauri.conf.json").version')
  mkdir -p /out/update
  for next in "$version" 99.0.0; do
    "$tauri" build --ci --bundles appimage --config "$(printf '{"version":"%s","plugins":{"updater":{"pubkey":"%s","endpoints":["http://127.0.0.1:8765/latest.json"],"dangerousInsecureTransportProtocol":true}}}' "$next" "$(cat "$key.pub")")"
    cp src-tauri/target/release/bundle/appimage/*_"$next"_*.AppImage "/out/update/Wavefunction-$next.AppImage"
  done
  "$tauri" signer sign --private-key-path "$key" --password "" /out/update/Wavefunction-99.0.0.AppImage
  arch=$(uname -m)
  printf '{"version":"99.0.0","notes":"Update check","pub_date":"2026-01-01T00:00:00Z","platforms":{"linux-%s":{"signature":"%s","url":"http://127.0.0.1:8765/Wavefunction-99.0.0.AppImage"}}}\n' \
    "$arch" "$(cat /out/update/Wavefunction-99.0.0.AppImage.sig)" > /out/update/latest.json
  echo "::endgroup::"

  echo "::group::tauri-driver, which the desktop checks read the app's page through"
  cargo install tauri-driver --locked --root "$HOME/tools"
  cp "$HOME/tools/bin/tauri-driver" /out/
  echo "::endgroup::"
}

desktop() {
  cd /build
  echo "::group::4. README's .deb install, on a system that has had nothing else installed"
  sudo apt install ./*.deb
  local package app
  package=$(dpkg-deb -f ./*.deb Package)
  app=$(dpkg -L "$package" | grep -m1 '^/usr/bin/')
  # The .deb brought everything it needs.
  command -v bwrap xdg-open
  if ldd "$app" | grep 'not found'; then exit 1; fi
  echo "::endgroup::"

  echo "::group::Tools for the checks, not the app: display, window manager, notifications, WebDriver"
  sudo apt-get install -y -q --no-install-recommends xvfb x11-utils openbox dunst dbus dbus-x11 \
    xdotool xclip ffmpeg webkit2gtk-driver fonts-dejavu-core
  echo "::endgroup::"

  # Artifacts lose their execute bit.
  install -m 755 /build/tauri-driver "$HOME/tauri-driver"
  dbus-run-session -- python3 /source/scripts/verify-linux-desktop.py \
    --app "$app" --update /build/update --driver "$HOME/tauri-driver" --out /out
}

"$stage"
