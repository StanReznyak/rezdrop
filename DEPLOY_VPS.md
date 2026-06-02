# Deploy RezDrop на VPS

VPS-сценарий для запуска: **Docker Compose + PostgreSQL + MinIO + FastAPI**.

## 1. Подготовка сервера

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

## 2. Клонирование

Склонируй репозиторий на сервер, перейди в папку проекта и создай `.env` из `.env.example`.

## 3. Настрой `.env`

Обязательно поменяй:

```env
APP_SECRET_KEY=<set long random secret key>
ADMIN_PASSWORD=<set strong admin password>
S3_SECRET_KEY=<set strong storage password>
PUBLIC_BASE_URL=https://example.com
COOKIE_SECURE=true
```

Для Docker по умолчанию используются:

```env
DATABASE_URL=<set database connection string on server>
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET_NAME=rezdrop
```

## 4. Запуск

```bash
docker compose up --build -d
```

Проверка:

```bash
docker compose ps
curl http://127.0.0.1:8080/health
```

## 5. Миграции

Миграции применяются автоматически в `docker-entrypoint.sh`:

```bash
alembic upgrade head
```

Вручную:

```bash
docker compose exec app alembic upgrade head
```

## 6. MinIO

MinIO Console:

```text
http://SERVER_IP:9001
```

Логин/пароль задаются через:

```env
S3_ACCESS_KEY
S3_SECRET_KEY
```

Bucket создаётся приложением автоматически, если включено:

```env
S3_AUTO_CREATE_BUCKET=true
```

## 7. Nginx + HTTPS

Для реального публичного запуска поставь Nginx и проксируй на `127.0.0.1:8080`.

Пример server block:

```nginx
server {
    server_name example.com;

    client_max_body_size 2048M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

HTTPS:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
```

## 8. Логи

```bash
docker compose logs -f app
docker compose logs -f db
docker compose logs -f minio
```

## 9. Обновление

```bash
git pull
docker compose up --build -d
docker compose exec app alembic upgrade head
```
