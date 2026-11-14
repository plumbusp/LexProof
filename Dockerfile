FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY samples ./samples
# The map's committed snapshot. The raw Exa/Gemini files in data/ stay local (see app/threatstore.py).
COPY mapdata ./mapdata
ENV DATA_DIR=/app/data
EXPOSE 8765
# Render (and most hosts) pass the port in $PORT; 8765 when run locally.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
