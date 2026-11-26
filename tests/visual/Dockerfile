FROM rust:1.94.1-bookworm@sha256:6ae102bdbf528294bc79ad6e1fae682f6f7c2a6e6621506ba959f9685b308a55
# Keep GTK, font rasterization and Xvfb versions identical across future runs.
RUN sed -i \
      -e 's@^URIs: http://deb.debian.org/debian$@URIs: https://snapshot.debian.org/archive/debian/20261125T000000Z/@' \
      -e 's@^URIs: http://deb.debian.org/debian-security$@URIs: https://snapshot.debian.org/archive/debian-security/20261125T000000Z/@' \
      /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends \
    at-spi2-core dbus-x11 fonts-dejavu-core gir1.2-gtk-3.0 git libgtk-3-dev \
    libsqlite3-dev python3-cairo python3-gi python3-gi-cairo python3-pil \
    x11-utils xauth xdotool xvfb && rm -rf /var/lib/apt/lists/*
ENV LANG=C.UTF-8 GTK_THEME=Adwaita GDK_BACKEND=x11 GTK_CSD=0 GTK_OVERLAY_SCROLLING=0
WORKDIR /work
