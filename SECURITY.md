# Security notes

В RezDrop есть базовая защита для локального файлового сервиса и простого VPS-запуска.

## Что уже есть

- CSRF-защита для браузерных POST-форм.
- Security headers: CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`.
- Настройка secure cookies для HTTPS.
- Trusted hosts через `ALLOWED_HOSTS`.
- Проверка слабых секретов при `APP_ENV=production`.
- Возможность использовать `ADMIN_PASSWORD_HASH` вместо обычного admin-пароля в `.env`.
- Простая проверка пароля пользователя: минимум 8 символов и блок очевидно слабых вариантов.
- Блокировка опасных расширений файлов.
- Проверка тестовой сигнатуры EICAR в простом режиме проверки файлов.
- Очистка доступна только после входа в админку и с CSRF-токеном.
- Dockerfile запускает приложение не от root.

## Что обязательно поменять перед VPS/production

1. Задать длинный `APP_SECRET_KEY`.
2. Поменять `ADMIN_PASSWORD` или использовать `ADMIN_PASSWORD_HASH`.
3. Поменять `S3_SECRET_KEY` / MinIO root password.
4. Включить `COOKIE_SECURE=true` при HTTPS.
5. Указать реальные `ALLOWED_HOSTS`.
6. Не коммитить `.env`.
7. Не открывать MinIO наружу без необходимости.
8. Поставить Nginx/HTTPS перед приложением.

## Хеш admin-пароля

Можно сгенерировать хеш так:

```bash
python scripts/make_password_hash.py
```

Потом добавить результат в `.env`:

```env
ADMIN_PASSWORD_HASH=<password-hash>
ADMIN_PASSWORD=
```

## Что стоит добавить дальше

- ClamAV вместо простой встроенной проверки.
- Redis для rate-limit.
- Отдельный worker для очистки файлов.
- Более подробные тесты безопасности.
- Логи и мониторинг для VPS.
