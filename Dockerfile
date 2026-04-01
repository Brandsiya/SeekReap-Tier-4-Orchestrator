FROM python:3.11-slim

# Install ffmpeg and chromaprint for audio fingerprinting
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tier4_main.py .

CMD ["python", "tier4_main.py"]
