FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=generate_report.settings.dev \
    QT_QPA_PLATFORM=offscreen \
    DWG_CONVERTER_PATH=/usr/local/bin/ODAFileConverter

WORKDIR /app

# mysqlclient needs the MariaDB/MySQL client headers at installation time.
# ODA File Converter also needs libxcb-util on current Ubuntu/Debian systems.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        libxcb-util1 \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Download the Linux x64 DEB package from the official ODA website and place it
# at vendor/ODAFileConverter.deb before running docker build.
COPY vendor/ODAFileConverter.deb /tmp/ODAFileConverter.deb
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/ODAFileConverter.deb \
    && rm -f /tmp/ODAFileConverter.deb \
    && if [ -e /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 ] \
          && [ ! -e /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 ]; then \
         ln -s libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0; \
       fi \
    && oda_bin="$(find /opt /usr -type f -name ODAFileConverter -perm /111 2>/dev/null | head -n 1)" \
    && test -n "$oda_bin" \
    && ln -sf "$oda_bin" /usr/local/bin/ODAFileConverter \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/generate_report/media \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "generate_report.wsgi:application"]
