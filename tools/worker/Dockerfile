FROM qmcgaw/gluetun@sha256:2733bb22b27e3efa7a9f2cef9057ec12791b8b225793fcd3dbfd0508404dfc25
RUN apk add --no-cache libgcc libstdc++
COPY published/ /tool/
ENV DOTNET_PROCESSOR_COUNT=2 HOME=/tmp XDG_DATA_HOME=/tmp/.local/share
USER 65534:65534
ENTRYPOINT ["/tool/DepotDownloaderMod"]
