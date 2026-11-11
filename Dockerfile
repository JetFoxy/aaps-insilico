# Runtime image. Build it with ./tools/docker_build.sh, which flattens the
# sim-bridge classpath produced by tools/bootstrap.sh into build/docker-lib.
FROM eclipse-temurin:21-jre-noble

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .
COPY build/docker-lib /opt/sim/lib

ENV SIM_CLASSPATH=/opt/sim/lib/sim-classpath.txt
ENV SIM_JAVA=/opt/java/openjdk/bin/java
ENV PYTHONPATH=/app/python

EXPOSE 8765 8766

CMD ["/opt/venv/bin/python", "python/server/live.py", "--host", "0.0.0.0", "--port", "8766"]
