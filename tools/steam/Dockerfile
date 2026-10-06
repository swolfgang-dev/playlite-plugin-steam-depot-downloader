FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 && sed -i 's/ main restricted/ main restricted universe multiverse/g' /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends steam-installer ca-certificates curl python3 socat xvfb x11vnc novnc websockify openbox xauth dbus-x11 procps unzip git libgl1-mesa-dri libgl1-mesa-dri:i386 libnss3 libasound2 libasound2:i386 libgtk2.0-0:i386 && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends jq libnotify-bin binutils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /home/steam /library /control && chown 65534:65534 /home/steam /library /control
ADD https://raw.githubusercontent.com/swwayps/luatools-moon/2c739b5349a972bb1932ac9457b0acf91b09e044/install.sh /opt/moon-install.sh
ADD https://raw.githubusercontent.com/swwayps/luatools-moon/2c739b5349a972bb1932ac9457b0acf91b09e044/LICENSE /opt/MOON-LICENSE
RUN echo "1c6896832d32d6d17c5635daf95b539763955f7ea1d9bd4446e24c2e8a5806c2  /opt/moon-install.sh" | sha256sum -c - && chmod 644 /opt/moon-install.sh /opt/MOON-LICENSE
COPY bridge.py setup_moon.py moon_bridge.lua steam_control.lua /opt/
ENV HOME=/home/steam DISPLAY=:1 XDG_CONFIG_HOME=/home/steam/.config XDG_DATA_HOME=/home/steam/.local/share
USER 65534:65534
ENTRYPOINT ["python3", "/opt/bridge.py"]
