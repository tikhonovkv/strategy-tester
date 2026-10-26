FROM php:8.1-alpine

ENV COMPOSER_VERSION=v2
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
CMD ["sh", "-c", "test -f vendor/autoload.php || composer install --no-dev --optimize-autoloader; php artisan serve --host=0.0.0.0 --port=80"]
