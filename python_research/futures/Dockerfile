FROM python:3.12-slim

RUN pip install --no-cache-dir numpy scikit-learn pillow
WORKDIR /app
COPY . /app/
ENV TZ=UTC PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# S3 settings come from build args (docker-compose build.args) and become runtime env.
ARG S3_BUCKET=""
ARG S3_ACCESS_KEY=""
ARG S3_SECRET_KEY=""
ARG S3_ENDPOINT="https://s3.twcstorage.ru"
ARG S3_REGION="ru-1"
ARG S3_PREFIX="bybit-futures"
ENV S3_BUCKET=$S3_BUCKET \
    S3_ACCESS_KEY=$S3_ACCESS_KEY \
    S3_SECRET_KEY=$S3_SECRET_KEY \
    S3_ENDPOINT=$S3_ENDPOINT \
    S3_REGION=$S3_REGION \
    S3_PREFIX=$S3_PREFIX

CMD ["python", "scheduler.py"]
