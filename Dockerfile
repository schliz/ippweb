FROM python:3.13-slim

# Install system dependencies
# libcups2-dev: for pycups
# libmagic1: for python-magic
# gcc, python3-dev: for building C extensions (pycups)
# libpq-dev: for psycopg2
RUN apt-get update && apt-get install -y \
    libcups2-dev \
    libmagic1 \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create uploads directory
RUN mkdir -p uploads && chmod 777 uploads

# Environment variables
ENV FLASK_APP=run.py
ENV FLASK_CONFIG=production
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# Run migrations and then start Gunicorn
CMD ["sh", "-c", "flask db upgrade && gunicorn -w 4 -b 0.0.0.0:5000 run:app"]
