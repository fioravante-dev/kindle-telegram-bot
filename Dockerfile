FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends calibre ghostscript qpdf python3 python3-pil ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV QT_QPA_PLATFORM=offscreen LANG=C.UTF-8 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY kindle_bot.py /app/
COPY scripts/ /app/scripts/
VOLUME /data
CMD ["python3", "/app/kindle_bot.py"]
