#!/usr/bin/env bash
# Розгортання і оновлення АвтоХаб КР на Ubuntu (запускати від користувача deploy з правом sudo).
#
#   Код уже лежить у /srv/autohubkr/app (git clone, git pull або розпакований архів).
#   SERVER_NAME=185.233.44.100 bash deploy/deploy.sh
#
# Скрипт ідемпотентний: повторний запуск оновлює залежності, міграції, статику й сервіси.
# Існуючий .env не змінюється. Сервер спільний з іншими проєктами, тому нічого глобального не чіпаємо:
# без оновлення системних пакетів, без firewall, лише власні файли, роль і база Postgres, власні unit-файли й server-блок nginx.
set -euo pipefail

BASE=${BASE:-/srv/autohubkr}
APP=${APP:-$BASE/app}
VENV=${VENV:-$BASE/venv}
SERVER_NAME=${SERVER_NAME:?вкажіть SERVER_NAME: IP або домен сайту}
PORT=${PORT:-8390}

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ПОМИЛКА: %s\n' "$*" >&2; exit 1; }

cd "$APP"
[ -f manage.py ] || die "у $APP немає manage.py: спершу покладіть туди код"

say "Python і virtualenv"
command -v python3 >/dev/null || die "python3 не знайдено"
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV" || die "немає модуля venv: sudo apt install python3-venv"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt

say ".env"
if [ -f .env ]; then
    echo ".env уже є, не чіпаю"
else
    SECRET_KEY=$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))')
    DB_PASSWORD=$("$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(24))')
    # umask лише в підоболонці: .env закритий від інших, а решта файлів (media, статика) лишається доступною nginx.
    (
        umask 077
        sed -e "s|__SECRET_KEY__|$SECRET_KEY|" \
            -e "s|__DB_PASSWORD__|$DB_PASSWORD|" \
            -e "s|__SERVER_NAME__|$SERVER_NAME|g" \
            deploy/env.production.template > .env
    )
    chmod 600 .env
    echo ".env створено: секрети згенеровано, ключі Gemini і Telegram порожні"
fi

# Значення з .env беремо без source: файл може містити пробіли й спецсимволи.
env_value() { grep -E "^$1=" .env | head -n1 | cut -d= -f2-; }
DB_NAME=$(env_value DB_NAME)
DB_USER=$(env_value DB_USER)
DB_PASSWORD=$(env_value DB_PASSWORD)

say "Postgres: роль і база $DB_NAME"
# Пароль іде через stdin, а не аргументами командного рядка: так його не видно в списку процесів.
case "$DB_USER$DB_NAME$DB_PASSWORD" in
    *[!A-Za-z0-9_]*) die "DB_USER, DB_NAME і DB_PASSWORD мають містити лише латиницю, цифри й _" ;;
esac
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
\set u '$DB_USER'
\set p '$DB_PASSWORD'
\set d '$DB_NAME'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'u', :'p')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'u')
\gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'u', :'p')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'d', :'u')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'d')
\gexec
SQL

say "Міграції, кеш, статика, довідники"
"$VENV/bin/python" manage.py check --deploy --fail-level ERROR
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py createcachetable
"$VENV/bin/python" manage.py collectstatic --noinput --verbosity 0
"$VENV/bin/python" manage.py seed_initial
mkdir -p media/providers

# nginx працює від www-data: каталоги на шляху мають бути прохідними, статика читається.
chmod o+x "$BASE" "$APP" media
chmod -R o+rX staticfiles media/providers

say "systemd"
sudo install -m 644 deploy/autohubkr-web.service /etc/systemd/system/autohubkr-web.service
sudo install -m 644 deploy/autohubkr-bot.service /etc/systemd/system/autohubkr-bot.service
sudo sed -i "s|127.0.0.1:[0-9]*|127.0.0.1:$PORT|" /etc/systemd/system/autohubkr-web.service
sudo systemctl daemon-reload
sudo systemctl enable autohubkr-web >/dev/null 2>&1
sudo systemctl restart autohubkr-web

# Бот без токена не запускається (команда runbot відмовляє), тому вмикаємо його лише коли токен уже вписано.
if [ -n "$(env_value TELEGRAM_BOT_TOKEN)" ]; then
    sudo systemctl enable autohubkr-bot >/dev/null 2>&1
    sudo systemctl restart autohubkr-bot
    echo "бот запущено"
else
    echo "TELEGRAM_BOT_TOKEN порожній: бот не запускаю (після вписування токена: sudo systemctl enable --now autohubkr-bot)"
fi

say "nginx"
CONF=/etc/nginx/sites-available/autohubkr
TMP=$(mktemp)
sed -e "s|__SERVER_NAME__|$SERVER_NAME|g" -e "s|127.0.0.1:[0-9]*;|127.0.0.1:$PORT;|" deploy/nginx-autohubkr.conf > "$TMP"
sudo install -m 644 "$TMP" "$CONF"
rm -f "$TMP"
sudo ln -sf "$CONF" /etc/nginx/sites-enabled/autohubkr
if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx
else
    sudo rm -f /etc/nginx/sites-enabled/autohubkr
    sudo nginx -t || true
    die "конфігурація nginx не пройшла перевірку, наш блок вимкнено, інші сайти не зачеплені"
fi

say "Перевірка"
sleep 2
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $SERVER_NAME" http://127.0.0.1/ || true)
echo "nginx -> сайт: HTTP $code"
[ "$code" = "200" ] || die "сайт відповів не 200; journalctl -u autohubkr-web -n 50"
echo "Готово: http://$SERVER_NAME/"
