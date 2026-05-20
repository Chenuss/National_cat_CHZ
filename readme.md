# National Catalog API Client

Python-клиент для работы с API Национального Каталога маркированных товаров (Честный Знак).

## Описание

Проект предназначен для получения данных о товарах из Национального Каталога через API и сохранения их в локальную базу данных SQLite для последующего анализа и использования.

## Установка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Создайте файл `.env` и добавьте ваш API ключ:
```
API_KEY=ваш_api_ключ
API_URL=https://апи.национальный-каталог.рф
DB_PATH=catalog.db
BATCH_SIZE=25
REQUEST_DELAY=1.0
```

## Использование

### Шаг 1: Получение списка товаров
Запустите скрипт для получения базового списка товаров:
```bash
python catalog_client.py
```

### Шаг 2: Миграция базы данных
Расширьте структуру БД для хранения полной информации:
```bash
python migrate_db.py
```

### Шаг 3: Загрузка атрибутов
Загрузите детальную информацию и атрибуты товаров:
```bash
python load_attributes.py
```

## Структура проекта

| Файл | Назначение |
|------|------------|
| `catalog_client.py` | Основной скрипт: получение списка товаров через /v4/product-list |
| `migrate_db.py` | Миграция БД: создание дополнительных таблиц и полей |
| `load_attributes.py` | Загрузка детальной информации через /v3/feed-product |
| `catalog.db` | База данных SQLite (создаётся автоматически) |
| `.env` | Файл с конфигурацией (API ключ, настройки) |
| `requirements.txt` | Зависимости Python |
| `PRD.md` | Документ с требованиями к продукту |
| `README.md` | Этот файл |

## База данных

Проект использует следующую структуру БД:

### Таблицы

- **products** - основная информация о товарах (расширенная)
  - good_id, gtin, good_name, tnved, brand_name, good_status
  - is_sim, is_kit, is_set, good_img, good_signed, good_mark_flag, good_turn_flag
  - create_date, update_date, first_sign_date
  - producer_inn, producer_name, brand_id

- **product_categories** - категории товаров
  - good_id, cat_id, cat_name

- **product_attributes** - атрибуты товаров
  - good_id, attr_id, attr_name, attr_value, attr_type, unit
  - is_required, first_layer

- **product_images** - изображения товаров
  - good_id, photo_type, image_url

- **product_identifiers** - идентификаторы товаров
  - good_id, identifier_type, identifier_value, packaging_level

## API Endpoints

### Продуктивная среда
- Base URL: `https://апи.национальный-каталог.рф`
- `/v4/product-list` - получение списка товаров
- `/v3/feed-product` - получение детальной информации о товарах

### Тестовая среда (sandbox)
- Base URL: `https://api.nk.sandbox.crptech.ru`

## Параметры запросов

### /v4/product-list
| Параметр | Описание |
|----------|----------|
| `from_date` | Дата начала периода (YYYY-MM-DD HH:MM:SS) |
| `to_date` | Дата окончания периода (YYYY-MM-DD HH:MM:SS) |
| `limit` | Количество записей (максимум 1000) |
| `good_status` | Статус карточки товара (например, published) |
| `apikey` | Ключ доступа API |

### /v3/feed-product
| Параметр | Описание |
|----------|----------|
| `good_ids` | Список ID товаров через `;` (максимум 25) |
| `gtins` | Список GTIN через `;` (максимум 25) |
| `apikey` | Ключ доступа API |

## Ограничения API

- Максимум 25 товаров в одном запросе к `/v3/feed-product`
- Лимит запросов: 500 запросов за 5 минут
- При превышении лимита возвращается статус 429 с заголовком `Retry-After`

## Настройка пакетной обработки

В файле `.env` можно настроить:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `BATCH_SIZE` | 25 | Размер пачки товаров для загрузки (макс. 25) |
| `REQUEST_DELAY` | 1.0 | Задержка между запросами в секундах |

## Примеры запросов к БД

```sql
-- Получить все товары определённой категории
SELECT p.good_id, p.good_name, c.cat_name
FROM products p
JOIN product_categories c ON p.good_id = c.good_id
WHERE c.cat_id = 12345;

-- Получить все атрибуты конкретного товара
SELECT attr_name, attr_value, unit
FROM product_attributes
WHERE good_id = 70871481;

-- Получить товары с изображениями
SELECT p.good_name, i.image_url
FROM products p
JOIN product_images i ON p.good_id = i.good_id;
```

## Лицензия

MIT
