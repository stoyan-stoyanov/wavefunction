#!/bin/sh
# Boot order matters: the X server must own the display before the window manager starts, and
# the action server goes last so a listening port means the desktop behind it is real.
set -eu

Xvfb :0 -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" -nolisten tcp &

# Wait for the display rather than sleeping a guessed amount.
for _ in $(seq 1 50); do
    if xdpyinfo -display :0 >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done

openbox &

# The desktop opens with the browser already running, and keeps one. Without it the screen is
# an empty rectangle with no launcher: a model spends its whole call budget hunting for a way to
# open anything — observed on the first live run — and a person who closes Firefox's last
# window while in control, which quits it, is left with nothing to click.
(while :; do
    firefox-esr --width 1280 --height 770 about:blank || :
    # A Firefox that restarted itself goes on running outside this loop, and a second one
    # beside it only says "Firefox is already running". Wait until every Firefox is gone.
    while pidof firefox-esr >/dev/null; do sleep 1; done
    sleep 1
done) &

# A responding HTTP endpoint must have a visible application behind it. Xvfb alone is
# already able to take a screenshot, but that screenshot is black during Firefox startup.
ready=0
for _ in $(seq 1 300); do
    if xdotool search --onlyvisible --class '[Ff]irefox' >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.1
done
if [ "$ready" -ne 1 ]; then
    echo "Firefox did not open a desktop window." >&2
    exit 1
fi

exec python3 /opt/server.py
