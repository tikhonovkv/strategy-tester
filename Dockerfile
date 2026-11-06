FROM php:8.1-alpine

ENV COMPOSER_VERSION=v2

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
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
RUN apk update && apk add git wget zip unzip libzip-dev oniguruma-dev libxml2-dev sqlite-dev
RUN docker-php-ext-install mysqli pdo pdo_mysql pdo_sqlite bcmath mbstring zip opcache
RUN docker-php-ext-enable pdo_mysql pdo_sqlite opcache
WORKDIR /var/www/html
COPY composer.json composer.lock* /var/www/html/
RUN composer install --no-dev --optimize-autoloader --no-scripts --no-interaction || true
COPY . /var/www/html/
RUN test -f vendor/autoload.php && composer dump-autoload --optimize || true
EXPOSE 80
CMD ["sh", "-c", "for k in S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY S3_ENDPOINT S3_REGION S3_PREFIX AWS_BUCKET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY BYBIT_API_KEY BYBIT_API_SECRET ACCOUNTS_CIPHER_KEY; do eval v=\\$$k; if [ -n \"$v\" ]; then grep -v \"^${k}=\" .env > .env.tmp 2>/dev/null || true; mv .env.tmp .env 2>/dev/null || true; printf \"%s=%s\\n\" \"$k\" \"$v\" >> .env; fi; done; test -f vendor/autoload.php || composer install --no-dev --optimize-autoloader; php artisan serve --host=0.0.0.0 --port=80 --no-reload"]
