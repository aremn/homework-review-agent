FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg ca-certificates libglib2.0-0 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libexpat1 libxcb1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libx11-xcb1 libxcursor1 libxi6 libxtst6 fonts-liberation libappindicator1 libindicator7 lsb-release xdg-utils \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY ./getcourse-reply-bot /app/getcourse-reply-bot
CMD ["python", "/app/getcourse-reply-bot/main_async.py"]
