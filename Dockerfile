FROM php:8.1-alpine

ENV COMPOSER_VERSION=v2
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
RUN apk update && apk add git wget zip unzip libzip-dev oniguruma-dev libxml2-dev sqlite-dev
RUN docker-php-ext-install mysqli pdo pdo_mysql pdo_sqlite bcmath mbstring zip opcache
RUN docker-php-ext-enable pdo_mysql pdo_sqlite opcache
WORKDIR /var/www/html
COPY . /var/www/html/
EXPOSE 80
# composer install на СТАРТЕ (в билде нет сети/зависимости), затем serve
CMD ["sh", "-c", "test -f vendor/autoload.php || composer install --no-dev --optimize-autoloader; php artisan serve --host=0.0.0.0 --port=80"]
