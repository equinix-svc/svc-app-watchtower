FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx
COPY backend /app/backend
WORKDIR /app/backend
EXPOSE 8800
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8800"]
