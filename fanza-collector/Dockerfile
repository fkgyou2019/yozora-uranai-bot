FROM public.ecr.aws/lambda/python:3.12

# Playwright Chromium の依存ライブラリ (Amazon Linux 2023)
RUN dnf install -y \
    atk \
    at-spi2-atk \
    cups-libs \
    gtk3 \
    libX11 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    libxkbcommon \
    mesa-libgbm \
    libdrm \
    alsa-lib \
    nss \
    nspr \
    pango \
    cairo \
    harfbuzz \
    && dnf clean all

# Python ライブラリ
RUN pip install --no-cache-dir playwright boto3

# Chromium をイメージ内に焼き込む
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install chromium

COPY collector.py ${LAMBDA_TASK_ROOT}/

CMD ["collector.handler"]
