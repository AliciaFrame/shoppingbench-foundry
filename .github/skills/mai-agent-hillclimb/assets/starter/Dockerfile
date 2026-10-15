FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY domain.py grader.py service.py judge-rubric.md ./
RUN useradd --uid 10001 --create-home appuser
USER appuser
EXPOSE 8080
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8080"]
