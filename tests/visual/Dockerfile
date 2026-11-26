FROM rust:1.94.1-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    at-spi2-core dbus-x11 fonts-dejavu-core gir1.2-gtk-3.0 git libgtk-3-dev \
    libsqlite3-dev python3-cairo python3-gi python3-gi-cairo python3-pil \
    python3-pyatspi xauth xdotool xvfb && rm -rf /var/lib/apt/lists/*
ENV LANG=C.UTF-8 GTK_THEME=Adwaita GDK_BACKEND=x11 GTK_CSD=0 GTK_OVERLAY_SCROLLING=0
WORKDIR /work
