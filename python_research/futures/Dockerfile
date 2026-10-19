FROM python:3.12-slim

RUN pip install --no-cache-dir numpy scikit-learn pillow
WORKDIR /app
COPY . /app/
ENV TZ=UTC PYTHONUNBUFFERED=1

CMD ["python", "scheduler.py"]
