FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 && sed -i 's/ main restricted/ main restricted universe multiverse/g' /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends steam-installer ca-certificates curl python3 socat xvfb x11vnc websockify openbox xauth dbus-x11 procps unzip git libgl1-mesa-dri libgl1-mesa-dri:i386 libnss3 libasound2 libasound2:i386 libgtk2.0-0:i386 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://codeload.github.com/novnc/noVNC/tar.gz/refs/tags/v1.7.0 -o /tmp/novnc.tar.gz && echo 'b1003a11b6e6e8d8f7f5e5586daae7f8ca651d8aee0aa155ff9ac841c48f52c6  /tmp/novnc.tar.gz' | sha256sum -c - && mkdir -p /usr/share/novnc && tar -xzf /tmp/novnc.tar.gz --strip-components=1 -C /usr/share/novnc && rm /tmp/novnc.tar.gz
COPY clipboard.js /usr/share/novnc/app/playlite-clipboard.js
RUN sed -i 's|</head>|<script type="module" src="app/playlite-clipboard.js"></script></head>|' /usr/share/novnc/vnc.html
RUN apt-get update && apt-get install -y --no-install-recommends jq libnotify-bin binutils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /home/steam /library /control && chown 65534:65534 /home/steam /library /control
# Vendored upstream recipe with Playlite's documented save-preservation patch.
COPY moon-install.sh MOON-LICENSE /opt/
RUN echo "37765e221b1f3b9597c3a965da15f3ebfe68676443e8784a928d8a26117e3947  /opt/moon-install.sh" | sha256sum -c - && chmod 644 /opt/moon-install.sh /opt/MOON-LICENSE
COPY bridge.py setup_moon.py moon_bridge.lua steam_control.lua retail_selection.py provider_limits.py /opt/
ENV HOME=/home/steam DISPLAY=:1 XDG_CONFIG_HOME=/home/steam/.config XDG_DATA_HOME=/home/steam/.local/share
USER 65534:65534
ENTRYPOINT ["python3", "/opt/bridge.py"]
