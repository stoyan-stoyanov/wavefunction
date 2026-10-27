# The desktop a computer-use agent drives. Built locally on the user's machine on first use —
# nothing is pulled from a registry beyond the Debian base, so the contents stay auditable.
#
# Firefox ESR is the one deliberate size cost: a desktop with nothing on it cannot do any
# useful task, and a browser is what most computer-use tasks drive.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        openbox \
        xdotool \
        scrot \
        x11vnc \
        xterm \
        x11-utils \
        firefox-esr \
        fonts-dejavu \
        python3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Windows fill the screen without a title bar. Its minimize button hid Firefox, and with no
# taskbar to bring it back, the person in control was left looking at a black screen.
RUN sed -i 's|</applications>|<application type="normal"><decor>no</decor><maximized>true</maximized></application></applications>|' \
        /etc/xdg/openbox/rc.xml \
    && grep -q '<application type="normal">' /etc/xdg/openbox/rc.xml

# An unprivileged user: the agent's clicks should not run as root even inside the container.
# Its home fills each new volume that keeps an agent's home, owner and all, and only at first
# use: whatever a desktop needs set up goes outside the home, or a kept home never gets it.
RUN useradd --create-home --shell /bin/bash desktop

COPY server.py /opt/server.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh
# Outside the home, which an agent's desktops keep in a volume: a changed setting reaches every
# kept profile with the next image, where one in the home would stay as the first desktop had it.
COPY firefox.js /etc/firefox-esr/wavefunction.js

USER desktop
ENV DISPLAY=:0 \
    SCREEN_WIDTH=1280 \
    SCREEN_HEIGHT=800

EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
