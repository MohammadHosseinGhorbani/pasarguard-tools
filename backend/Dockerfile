FROM python:3.12-slim

WORKDIR /app

# Chromium + its driver power the Proton VPN login (a real headless browser
# beats Proton's bot defenses that trip a plain HTTP/SRP handshake).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        ca-certificates \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Point Selenium at the installed browser/driver so it need not fetch either.
ENV PROTON_CHROME_BINARY=/usr/bin/chromium \
    PROTON_CHROMEDRIVER=/usr/bin/chromedriver

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
