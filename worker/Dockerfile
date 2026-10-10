FROM gradle:8.14.4-jdk21-jammy@sha256:e72e4c21e080acef98ef3b3c42c9bf7d01882a7bd96b86b87d1a60ac90cad066
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 xvfb xauth libgl1-mesa-dri libglx-mesa0 libxrandr2 libxinerama1 \
    libxcursor1 libxi6 libasound2 libpulse0 pciutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG NEOFORGE_VERSION=21.1.248
COPY client-mod/build.gradle client-mod/gradle.properties client-mod/settings.gradle /opt/mvt/client-mod/
WORKDIR /opt/mvt/client-mod
RUN gradle -g /opt/mvt/gradle-cache --no-daemon -Pneo_version=$NEOFORGE_VERSION createMinecraftArtifacts downloadAssets
COPY client-mod/src /opt/mvt/client-mod/src
COPY worker/launch.py /opt/mvt/launch.py
COPY worker/authenticated.sh /opt/mvt/authenticated.sh
RUN gradle -g /opt/mvt/gradle-cache --no-daemon -Pneo_version=$NEOFORGE_VERSION build exportMvtLaunch \
    && chmod 755 /opt/mvt/authenticated.sh \
    && chmod -R a+rX /opt/mvt \
    && mkdir -p /game /control
LABEL org.cha0s.mvt.protocol="1" org.cha0s.mvt.neoforge=$NEOFORGE_VERSION
ENV LIBGL_ALWAYS_SOFTWARE=true GALLIUM_DRIVER=llvmpipe ALSOFT_DRIVERS=null
WORKDIR /game
ENTRYPOINT ["python3", "/opt/mvt/launch.py"]
