FROM php:8.1-alpine

ENV COMPOSER_VERSION=v2
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
RUN apk update && apk add git wget zip unzip
RUN docker-php-ext-install mysqli pdo pdo_mysql && docker-php-ext-enable pdo_mysql
WORKDIR /var/www/html
ENTRYPOINT ["tail", "-f", "/dev/null"]