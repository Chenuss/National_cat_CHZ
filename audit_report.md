# Аудит кода интеграции с API Национального Каталога (v.5.59)

## Шаг 1. Анализ по чек-листу

| # | Эталонный элемент | Реализовано? | Комментарий |
|---|---|---|---|
| 1 | Аутентификация (apikey / bearer) | ⚠️ | Реализован только `apikey` через query-параметр. Bearer token не поддерживается. Ключ хранится в `.env` — это правильно. |
| 2 | Загрузка справочника категорий | ❌ | Отсутствует полностью. Нет метода для `GET /v3/categories`. |
| 3 | Загрузка справочника брендов | ❌ | Отсутствует полностью. Нет метода для `GET /v3/brands`. |
| 4 | Загрузка стран (isocountry) | ❌ | Отсутствует полностью. Нет метода для `GET /v3/dictionary/isocountry`. |
| 5 | Загрузка атрибутивных моделей | ❌ | Отсутствует полностью. Нет метода для `GET /v3/attributes`. |
| 6 | Пагинация в /v4/product-list | ⚠️ | Есть параметр `limit`, но нет обработки `offset` для полной пагинации. За один запрос — только 1000 записей. |
| 7 | Нарезка по from_date/to_date при >10к товаров | ❌ | Отсутствует. Хардкод диапазона 2020-2026, что может превысить лимит 10 000 записей. |
| 8 | Загрузка полных карточек через /v3/feed-product | ✅ | Реализовано в `load_attributes.py`. Используется правильный метод. |
| 9 | Батчинг по 25 gtins/good_ids | ✅ | Реализовано: `BATCH_SIZE = 25`. Правильно используется разделитель `;`. |
| 10 | Сохранение и использование ETag | ❌ | В коде есть проверка на `304`, но ETag не сохраняется в БД и не используется в заголовке `If-None-Match`. |
| 11 | Инкрементальное обновление через /v3/etagslist | ❌ | Отсутствует полностью. Нет метода `GET /v3/etagslist`. |
| 12 | Загрузка разрешительных документов | ❌ | Отсутствует полностью. Нет методов `POST /v4/rd-info-by-gtin` или `/v4/rd-info`. |
| 13 | Скачивание изображений | ⚠️ | URL изображений сохраняются в БД, но бинарное скачивание в локальное хранилище не реализовано. |
| 14 | Модель БД: уровни упаковки | ⚠️ | Таблица `product_identifiers` существует, но поле `packaging_level` не соответствует структуре НК (`level` вместо `packaging_level`). Нет отдельной сущности «Упаковка» с атрибутами. |
| 15 | Модель БД: атрибуты как EAV | ✅ | Таблица `product_attributes` реализует паттерн EAV correctly. |
| 16 | Обработка наборов (is_set) и комплектов (is_kit) | ⚠️ | Поля `is_set` и `is_kit` добавлены в таблицу `products`, но массив `set_gtins` не обрабатывается и не сохраняется. |
| 17 | Обработка субаккаунтов | ❌ | Параметр `subaccount=true` не используется в запросах к `/v3/feed-product`. |
| 18 | Обработка HTTP 429 + Retry-After | ⚠️ | В `load_attributes.py` есть базовая обработка, но нет паузы 5 минут при исчерпании лимита серии. |
| 19 | Экспоненциальный backoff на 5xx | ❌ | Отсутствует. Нет retry-логики для 500/503 ошибок. |
| 20 | Чтение заголовков API-Usage-Limit | ⚠️ | В `catalog_client.py` заголовок логируется, но не используется для контроля rate limiting. |
| 21 | Кодировка БД utf8mb4 | ⚠️ | SQLite использует UTF-8 по умолчанию, но явного указания кодировки нет. Для PostgreSQL потребуется `utf8mb4`. |
| 22 | Идемпотентность (UPSERT по good_id / gtin) | ✅ | Реализовано через `INSERT ... ON CONFLICT(good_id) DO UPDATE`. |

---

## Шаг 2. План доработки

### 1. Критичные баги (P0)

| Баг | Описание | Риск |
|-----|----------|------|
| **Отсутствие полной пагинации** | `product-list` вызывается без `offset`, возвращает только 1000 товаров | Потеря данных при количестве товаров > 1000 |
| **Превышение лимита 10 000 записей** | Хардкод диапазона 2020-2026 без нарезки по периодам | API вернёт ошибку или обрежет данные |
| **ETag не используется** | Запросы к `/v3/feed-product` всегда полные, тратят лимит | Быстрое исчерпание лимита 500 запросов |
| **Нет инкрементального обновления** |每次 запуска — полная загрузка всех товаров | Невозможность работы в production |
| **Отсутствие retry на 5xx** | При временной ошибке сервера процесс падает | Нестабильность синхронизации |
| **Не обрабатываются наборы** | `set_gtins` игнорируется | Потеря информации о вложенных товарах |

---

### 2. Архитектурные изменения (схема БД)

```sql
-- ============================================
-- СПРАВОЧНИКИ (ЭТАП 1)
-- ============================================

-- Категории (из /v3/categories)
CREATE TABLE IF NOT EXISTS categories (
    cat_id INTEGER PRIMARY KEY,
    cat_name TEXT NOT NULL,
    cat_parent_id INTEGER REFERENCES categories(cat_id),
    cat_level INTEGER NOT NULL,
    category_active BOOLEAN DEFAULT TRUE,
    gismt_codes TEXT, -- JSON массив кодов товарных групп
    etag TEXT, -- Для инкрементального обновления
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Бренды (из /v3/brands)
CREATE TABLE IF NOT EXISTS brands (
    brand_id INTEGER PRIMARY KEY,
    brand_name TEXT NOT NULL,
    etag TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Страны (из /v3/dictionary/isocountry)
CREATE TABLE IF NOT EXISTS countries (
    country_code TEXT PRIMARY KEY, -- ISO Alpha-2
    country_name TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Атрибутивные модели (из /v3/attributes)
CREATE TABLE IF NOT EXISTS attribute_models (
    attr_id INTEGER PRIMARY KEY,
    attr_name TEXT NOT NULL,
    attr_type TEXT,
    cat_id INTEGER REFERENCES categories(cat_id),
    tnved TEXT,
    is_required BOOLEAN DEFAULT FALSE,
    first_layer BOOLEAN DEFAULT FALSE,
    second_layer BOOLEAN DEFAULT FALSE,
    multiplicity_type TEXT, -- 'single', 'multiple', 'preset'
    preset_url TEXT,
    unit TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- ТОВАРЫ (расширение существующей таблицы)
-- ============================================

ALTER TABLE products ADD COLUMN etag TEXT;
ALTER TABLE products ADD COLUMN subaccount_owner TEXT; -- ИНН владельца при subaccount=true
ALTER TABLE products ADD COLUMN remainder_type TEXT; -- full / short / null

-- ============================================
-- УРОВНИ УПАКОВКИ (ЭТАП 6)
-- ============================================

CREATE TABLE IF NOT EXISTS product_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL, -- 'gtin', 'metro-unit', etc.
    identifier_value TEXT NOT NULL,
    level TEXT NOT NULL, -- trade-unit, inner-pack, box, layer, pallet, metro-unit, show-pack
    multiplier INTEGER DEFAULT 1,
    -- Атрибуты упаковки (габариты, вес, материал)
    length_mm NUMERIC,
    width_mm NUMERIC,
    height_mm NUMERIC,
    weight_g NUMERIC,
    packaging_material TEXT,
    UNIQUE(good_id, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_packages_good_id ON product_packages(good_id);
CREATE INDEX IF NOT EXISTS idx_packages_level ON product_packages(level);

-- ============================================
-- НАБОРЫ И КОМПЛЕКТЫ (ЭТАП 7)
-- ============================================

CREATE TABLE IF NOT EXISTS product_set_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    child_gtin TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    UNIQUE(parent_good_id, child_gtin)
);

CREATE INDEX IF NOT EXISTS idx_set_items_parent ON product_set_items(parent_good_id);

-- ============================================
-- ИЗОБРАЖЕНИЯ (доработка ЭТАП 5)
-- ============================================

ALTER TABLE product_images ADD COLUMN local_path TEXT; -- Путь к локальному файлу
ALTER TABLE product_images ADD COLUMN downloaded_at TIMESTAMP;
ALTER TABLE product_images ADD COLUMN photo_hash TEXT; -- Для дедупликации

-- ============================================
-- РАЗРЕШИТЕЛЬНЫЕ ДОКУМЕНТЫ (ЭТАП 4)
-- ============================================

CREATE TABLE IF NOT EXISTS certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    good_id INTEGER REFERENCES products(good_id) ON DELETE SET NULL,
    gtin TEXT,
    rd_number TEXT NOT NULL,
    rd_date DATE,
    rd_type TEXT, -- 'certificate', 'declaration', 'sgr'
    attr_id INTEGER, -- 23561 / 23557 / 23765
    status TEXT,
    status_group TEXT,
    from_date DATE,
    to_date DATE,
    applicant TEXT,
    manufacturer TEXT,
    product_tnved TEXT,
    product_tech_regulations TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(rd_number, rd_date)
);

CREATE INDEX IF NOT EXISTS idx_certificates_good_id ON certificates(good_id);
CREATE INDEX IF NOT EXISTS idx_certificates_gtin ON certificates(gtin);

-- ============================================
-- ЛОГ СИНХРОНИЗАЦИИ
-- ============================================

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL, -- 'full', 'incremental', 'catalogs', 'certificates'
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT, -- 'running', 'success', 'error'
    records_processed INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_log_started ON sync_log(started_at);
```

---

### 3. Новые модули / классы

| Файл | Назначение |
|------|------------|
| `api_client.py` | Единый клиент API с retry-логикой, ETag, rate limiting |
| `sync_manager.py` | Оркестратор синхронизации (полная / инкрементальная) |
| `catalogs_loader.py` | Загрузка справочников (категории, бренды, страны, атрибуты) |
| `images_downloader.py` | Скачивание изображений в локальное хранилище |
| `certificates_loader.py` | Загрузка разрешительных документов |
| `config.py` | Конфигурация и константы |
| `db_schema.sql` | DDL схема БД |
| `main.py` | Точка входа с CLI аргументами |

---

### 4. Пошаговый roadmap

| Этап | Задача | Сложность | Оценка времени |
|------|--------|-----------|----------------|
| **1** | Исправить пагинацию в `product-list` (offset + нарезка по датам) | 🟢 | 2 часа |
| **2** | Создать класс API-клиента с retry и ETag | 🟡 | 4 часа |
| **3** | Реализовать инкрементальное обновление через `etagslist` | 🟡 | 4 часа |
| **4** | Расширить схему БД (таблицы из раздела 2) | 🟢 | 2 часа |
| **5** | Загрузка справочников (categories, brands, countries, attributes) | 🟡 | 6 часов |
| **6** | Обработка уровней упаковки (product_packages) | 🟡 | 4 часа |
| **7** | Обработка наборов (set_gtins) и комплектов (is_kit) | 🟡 | 3 часа |
| **8** | Скачивание изображений в локальное хранилище | 🟢 | 3 часа |
| **9** | Загрузка разрешительных документов (rd-info-by-gtin) | 🟡 | 4 часа |
| **10** | Обработка субаккаунтов (параметр subaccount) | 🟢 | 1 час |
| **11** | Rate limiting по заголовкам API-Usage-Limit | 🟡 | 3 часа |
| **12** | Логирование и мониторинг синхронизации | 🟢 | 2 часа |

**Итого:** ~38 часов (~5 рабочих дней)

---

### 5. Фрагменты кода (эталонные сниппеты)

#### 5.1. Класс-клиент API с ETag и retry-логикой

```python
#!/usr/bin/env python3
"""
api_client.py — Клиент API Национального Каталога с поддержкой:
- ETag (If-None-Match)
- Retry с экспоненциальным backoff
- Rate limiting по заголовкам API-Usage-Limit
- Обработка HTTP 429 (Retry-After)
"""

import os
import time
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class RateLimitExceededError(Exception):
    """Превышен лимит запросов API"""
    pass


class NationalCatalogAPIClient:
    """Клиент API Национального Каталога с продвинутой обработкой ошибок."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 300.0  # 5 минут
    ):
        self.api_key = api_key or os.getenv('API_KEY')
        self.api_url = api_url or os.getenv('API_URL', 'https://апи.национальный-каталог.рф')
        
        if not self.api_key:
            raise ValueError("API ключ не найден")
        
        self.base_url = self.api_url.rstrip('/')
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        
        self.session = requests.Session()
        # Предпочитаем заголовок Authorization для безопасности
        # Но поддерживаем и apikey в query params
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'NK-Sync-Client/1.0'
        })
        
        # Для отслеживания лимитов
        self._api_usage_remaining: Optional[int] = None
        self._method_usage_remaining: Optional[int] = None
        self._series_start_time: Optional[datetime] = None
        
        logger.info(f"Инициализирован API клиент: {self.api_url}")
    
    def _get_auth_params(self) -> Dict[str, str]:
        """Возвращает параметры аутентификации."""
        return {'apikey': self.api_key}
    
    def _update_rate_limits(self, headers: Dict[str, str]) -> None:
        """Обновляет счётчики лимитов из заголовков ответа."""
        usage_limit = headers.get('API-Usage-Limit', '')
        if usage_limit and '/' in usage_limit:
            try:
                current, total = map(int, usage_limit.split('/'))
                self._api_usage_remaining = total - current
                logger.debug(f"API Usage: {current}/{total} (осталось: {self._api_usage_remaining})")
            except ValueError:
                pass
        
        method_limit = headers.get('API-Method-Usage-Limit', '')
        if method_limit and '/' in method_limit:
            try:
                current, total = map(int, method_limit.split('/'))
                self._method_usage_remaining = total - current
            except ValueError:
                pass
    
    def _handle_rate_limit(self, retry_after: Optional[int] = None) -> None:
        """Обработка превышения лимита запросов."""
        if retry_after is None:
            # Если Retry-After не указан, ждём 5 минут до начала новой серии
            retry_after = 300
            logger.warning("Лимит запросов исчерпан. Пауза 5 минут до начала новой серии.")
        else:
            logger.warning(f"Превышен лимит запросов. Пауза {retry_after} секунд.")
        
        time.sleep(retry_after)
        self._series_start_time = None  # Сброс серии
    
    def request(
        self,
        method: str,
        endpoint: str,
        etag: Optional[str] = None,
        use_auth: bool = True,
        **kwargs
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
        """
        Выполняет HTTP-запрос с retry-логикой и поддержкой ETag.
        
        Args:
            method: HTTP метод (GET, POST, etc.)
            endpoint: Относительный путь endpoint (например, '/v3/feed-product')
            etag: Сохранённый ETag для заголовка If-None-Match
            use_auth: Добавлять ли параметры аутентификации
            **kwargs: Дополнительные параметры для requests.request()
        
        Returns:
            Кортеж (data, new_etag, was_modified):
            - data: Распарсенные JSON-данные (None если 304)
            - new_etag: Новый ETag из ответа
            - was_modified: True если данные изменились (не 304)
        """
        url = f"{self.base_url}{endpoint}"
        
        if use_auth:
            if 'params' not in kwargs:
                kwargs['params'] = {}
            kwargs['params'].update(self._get_auth_params())
        
        headers = kwargs.pop('headers', {})
        if etag:
            headers['If-None-Match'] = etag
            logger.debug(f"Using ETag: {etag}")
        
        retry_count = 0
        last_exception: Optional[Exception] = None
        
        while retry_count <= self.max_retries:
            try:
                response = self.session.request(method, url, headers=headers, **kwargs)
                
                # Обновляем счётчики лимитов
                self._update_rate_limits(response.headers)
                
                # Получаем новый ETag
                new_etag = response.headers.get('ETag')
                
                # Обработка 304 Not Modified
                if response.status_code == 304:
                    logger.info(f"Данные не изменились (ETag match): {endpoint}")
                    return None, new_etag, False
                
                # Обработка 429 Too Many Requests
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    retry_after_int = int(retry_after) if retry_after else None
                    self._handle_rate_limit(retry_after_int)
                    retry_count += 1
                    continue
                
                # Обработка 5xx Server Errors
                if response.status_code >= 500:
                    retry_count += 1
                    if retry_count > self.max_retries:
                        logger.error(f"Превышено количество retries ({self.max_retries}) для {endpoint}")
                        raise RateLimitExceededError("Сервер недоступен после нескольких попыток")
                    
                    # Экспоненциальный backoff
                    delay = min(self.base_delay * (2 ** retry_count), self.max_delay)
                    logger.warning(f"Ошибка {response.status_code}. Повтор через {delay:.1f} сек (попытка {retry_count}/{self.max_retries})")
                    time.sleep(delay)
                    continue
                
                # Обработка других ошибок
                response.raise_for_status()
                
                # Парсинг JSON
                data = response.json() if response.content else None
                
                logger.debug(f"Успешный запрос: {endpoint}, статус: {response.status_code}")
                return data, new_etag, True
                
            except requests.exceptions.RequestException as e:
                last_exception = e
                retry_count += 1
                
                if retry_count > self.max_retries:
                    logger.error(f"Все попытки исчерпаны для {endpoint}: {e}")
                    break
                
                delay = min(self.base_delay * (2 ** retry_count), self.max_delay)
                logger.warning(f"Ошибка соединения: {e}. Повтор через {delay:.1f} сек")
                time.sleep(delay)
        
        raise last_exception or RateLimitExceededError("Запрос не выполнен после всех попыток")
    
    def get(self, endpoint: str, etag: Optional[str] = None, **kwargs) -> Tuple[Optional[Dict], Optional[str], bool]:
        """GET-запрос с поддержкой ETag."""
        return self.request('GET', endpoint, etag=etag, **kwargs)
    
    def post(self, endpoint: str, json: Optional[Dict] = None, **kwargs) -> Tuple[Optional[Dict], Optional[str], bool]:
        """POST-запрос."""
        return self.request('POST', endpoint, json=json, use_auth=True, **kwargs)
```

---

#### 5.2. Функция батчевой загрузки `/v3/feed-product` по 25 good_id

```python
def fetch_products_batch(
    client: NationalCatalogAPIClient,
    good_ids: list[int],
    subaccount: bool = False
) -> list[dict]:
    """
    Загружает полные карточки товаров батчем до 25 штук.
    
    Args:
        client: Экземпляр API клиента
        good_ids: Список good_id (максимум 25)
        subaccount: Запрашивать ли данные субаккаунтов
    
    Returns:
        Список карточек товаров
    """
    if not good_ids:
        return []
    
    if len(good_ids) > 25:
        logger.warning(f"Слишком много good_ids ({len(good_ids)}). Обрезаем до 25.")
        good_ids = good_ids[:25]
    
    endpoint = '/v3/feed-product'
    params = {
        'good_ids': ';'.join(map(str, good_ids)),
        'subaccount': 'true' if subaccount else 'false'
    }
    
    try:
        data, etag, modified = client.get(endpoint, params=params)
        
        if not modified:
            logger.info(f"Данные для батча {good_ids} не изменились")
            return []
        
        if not data:
            return []
        
        # Извлекаем товары из структуры ответа
        if isinstance(data, dict):
            if 'result' in data:
                result = data['result']
                if isinstance(result, dict) and 'goods' in result:
                    products = result['goods']
                elif isinstance(result, list):
                    products = result
                else:
                    products = []
            elif 'goods' in data:
                products = data['goods']
            else:
                products = [data] if 'good_id' in data else []
        elif isinstance(data, list):
            products = data
        else:
            products = []
        
        logger.info(f"Загружено {len(products)} товаров из батча {len(good_ids)}")
        return products
        
    except Exception as e:
        logger.error(f"Ошибка загрузки батча {good_ids}: {e}")
        return []
```

---

#### 5.3. Инкрементальное обновление через `/v3/etagslist`

```python
def sync_incremental(
    client: NationalCatalogAPIClient,
    db: CatalogDatabase,
    batch_size: int = 100
) -> dict:
    """
    Выполняет инкрементальную синхронизацию товаров.
    
    Алгоритм:
    1. Загружаем список всех good_id + etag из БД
    2. Запрашиваем /v3/etagslist — получаем актуальные etag от API
    3. Сравниваем — выявляем изменённые товары
    4. Загружаем только изменённые через /v3/feed-product
    
    Returns:
        Статистика синхронизации
    """
    stats = {
        'local_count': 0,
        'remote_count': 0,
        'changed_count': 0,
        'updated_count': 0,
        'new_count': 0
    }
    
    # 1. Получаем локальные ETag
    local_etags = db.get_all_etags()  # Dict[good_id, etag]
    stats['local_count'] = len(local_etags)
    
    # 2. Загружаем удалённый список ETag
    # Примечание: API не имеет прямого метода etagslist для всех товаров
    # Поэтому используем product-list для получения списка good_id
    # И затем сравниваем по feed-product с сохранёнными ETag
    
    # Альтернатива: загружаем все good_id через product-list с пагинацией
    all_good_ids = []
    offset = 0
    limit = 1000
    
    while True:
        params = {
            'limit': limit,
            'offset': offset,
            'from_date': '2020-01-01 00:00:00',
            'to_date': '2026-12-31 23:59:59'
        }
        
        data, _, _ = client.get('/v4/product-list', params=params)
        
        if not data:
            break
        
        goods = data.get('result', {}).get('goods', []) if isinstance(data, dict) else data
        if not goods:
            break
        
        for good in goods:
            good_id = good.get('good_id')
            if good_id:
                all_good_ids.append(good_id)
        
        stats['remote_count'] = len(all_good_ids)
        
        if len(goods) < limit:
            break
        
        offset += limit
        time.sleep(0.5)  # Rate limiting
    
    # 3. Выявляем новые и изменённые товары
    remote_good_ids = set(all_good_ids)
    local_good_ids = set(local_etags.keys())
    
    new_good_ids = remote_good_ids - local_good_ids
    existing_good_ids = remote_good_ids & local_good_ids
    
    stats['new_count'] = len(new_good_ids)
    
    # Для существующих — проверяем изменения через feed-product с If-None-Match
    changed_good_ids = []
    
    for good_id in existing_good_ids:
        etag = local_etags.get(good_id)
        data, new_etag, modified = client.get(
            '/v3/feed-product',
            params={'good_ids': str(good_id)},
            etag=etag
        )
        
        if modified:
            changed_good_ids.append(good_id)
            # Обновляем ETag в БД
            db.update_product_etag(good_id, new_etag)
    
    stats['changed_count'] = len(changed_good_ids)
    
    # 4. Загружаем полные данные для новых и изменённых
    all_to_update = list(new_good_ids) + changed_good_ids
    
    for i in range(0, len(all_to_update), batch_size):
        batch = all_to_update[i:i+batch_size]
        products = fetch_products_batch(client, batch, subaccount=True)
        
        if products:
            db.save_products(products)
            stats['updated_count'] += len(products)
        
        time.sleep(1.0)  # Rate limiting
    
    logger.info(f"Инкрементальная синхронизация завершена: {stats}")
    return stats
```

---

#### 5.4. Схема БД (PostgreSQL DDL)

```sql
-- Файл: db_schema.sql
-- Диалект: PostgreSQL 14+
-- Кодировка: utf8mb4 (UTF-8 в PostgreSQL по умолчанию)

SET client_encoding TO 'UTF8';

-- ============================================
-- СПРАВОЧНИКИ
-- ============================================

CREATE TABLE IF NOT EXISTS categories (
    cat_id INTEGER PRIMARY KEY,
    cat_name VARCHAR(500) NOT NULL,
    cat_parent_id INTEGER REFERENCES categories(cat_id),
    cat_level INTEGER NOT NULL CHECK (cat_level >= 0),
    category_active BOOLEAN DEFAULT TRUE,
    gismt_codes JSONB,
    etag VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_categories_parent ON categories(cat_parent_id);
CREATE INDEX idx_categories_level ON categories(cat_level);

CREATE TABLE IF NOT EXISTS brands (
    brand_id INTEGER PRIMARY KEY,
    brand_name VARCHAR(500) NOT NULL,
    etag VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS countries (
    country_code CHAR(2) PRIMARY KEY CHECK (length(country_code) = 2),
    country_name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attribute_models (
    attr_id INTEGER PRIMARY KEY,
    attr_name VARCHAR(200) NOT NULL,
    attr_type VARCHAR(50),
    cat_id INTEGER REFERENCES categories(cat_id),
    tnved VARCHAR(20),
    is_required BOOLEAN DEFAULT FALSE,
    first_layer BOOLEAN DEFAULT FALSE,
    second_layer BOOLEAN DEFAULT FALSE,
    multiplicity_type VARCHAR(20) CHECK (multiplicity_type IN ('single', 'multiple', 'preset')),
    preset_url TEXT,
    unit VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_attr_models_cat ON attribute_models(cat_id);
CREATE INDEX idx_attr_models_tnved ON attribute_models(tnved);

-- ============================================
-- ТОВАРЫ
-- ============================================

CREATE TABLE IF NOT EXISTS products (
    good_id INTEGER PRIMARY KEY,
    gtin VARCHAR(14),
    good_name TEXT,
    tnved VARCHAR(20),
    brand_name VARCHAR(500),
    brand_id INTEGER REFERENCES brands(brand_id),
    good_status VARCHAR(50),
    good_detailed_status JSONB,
    
    -- Расширенные поля из feed-product
    is_sim BOOLEAN DEFAULT FALSE,
    is_kit BOOLEAN DEFAULT FALSE,
    is_set BOOLEAN DEFAULT FALSE,
    good_img TEXT,
    good_signed BOOLEAN DEFAULT FALSE,
    good_mark_flag BOOLEAN DEFAULT FALSE,
    good_turn_flag BOOLEAN DEFAULT FALSE,
    flags_updated_date TIMESTAMP WITH TIME ZONE,
    create_date TIMESTAMP WITH TIME ZONE,
    update_date TIMESTAMP WITH TIME ZONE,
    first_sign_date TIMESTAMP WITH TIME ZONE,
    producer_inn VARCHAR(12),
    producer_name TEXT,
    
    -- Для инкрементальной синхронизации
    etag VARCHAR(100),
    subaccount_owner VARCHAR(12), -- ИНН владельца
    remainder_type VARCHAR(10) CHECK (remainder_type IN ('full', 'short')),
    
    -- Метаданные
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_products_gtin ON products(gtin);
CREATE INDEX idx_products_status ON products(good_status);
CREATE INDEX idx_products_brand ON products(brand_id);
CREATE INDEX idx_products_producer_inn ON products(producer_inn);

-- ============================================
-- АТРИБУТЫ (EAV)
-- ============================================

CREATE TABLE IF NOT EXISTS product_attributes (
    id BIGSERIAL PRIMARY KEY,
    good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    attr_id INTEGER NOT NULL REFERENCES attribute_models(attr_id),
    attr_name VARCHAR(200) NOT NULL,
    attr_value TEXT,
    attr_type VARCHAR(50),
    unit VARCHAR(50),
    is_required BOOLEAN DEFAULT FALSE,
    first_layer BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_attrs_good ON product_attributes(good_id);
CREATE INDEX idx_attrs_attr ON product_attributes(attr_id);
CREATE INDEX idx_attrs_value ON product_attributes USING gin(to_tsvector('russian', attr_value));

-- ============================================
-- УПАКОВКИ
-- ============================================

CREATE TABLE IF NOT EXISTS product_packages (
    id BIGSERIAL PRIMARY KEY,
    good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    identifier_type VARCHAR(50) NOT NULL,
    identifier_value VARCHAR(100) NOT NULL,
    level VARCHAR(50) NOT NULL CHECK (level IN (
        'trade-unit', 'inner-pack', 'box', 'layer', 
        'pallet', 'metro-unit', 'show-pack'
    )),
    multiplier INTEGER DEFAULT 1 CHECK (multiplier > 0),
    
    -- Габариты и вес
    length_mm NUMERIC(10,2),
    width_mm NUMERIC(10,2),
    height_mm NUMERIC(10,2),
    weight_g NUMERIC(10,2),
    
    -- Материал упаковки
    packaging_material VARCHAR(200),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uniq_package UNIQUE (good_id, identifier_value)
);

CREATE INDEX idx_packages_good ON product_packages(good_id);
CREATE INDEX idx_packages_level ON product_packages(level);

-- ============================================
-- НАБОРЫ
-- ============================================

CREATE TABLE IF NOT EXISTS product_set_items (
    id BIGSERIAL PRIMARY KEY,
    parent_good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    child_gtin VARCHAR(14) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uniq_set_item UNIQUE (parent_good_id, child_gtin)
);

CREATE INDEX idx_set_items_parent ON product_set_items(parent_good_id);

-- ============================================
-- ИЗОБРАЖЕНИЯ
-- ============================================

CREATE TABLE IF NOT EXISTS product_images (
    id BIGSERIAL PRIMARY KEY,
    good_id INTEGER NOT NULL REFERENCES products(good_id) ON DELETE CASCADE,
    photo_type VARCHAR(50),
    image_url TEXT NOT NULL,
    local_path TEXT,
    downloaded_at TIMESTAMP WITH TIME ZONE,
    photo_hash VARCHAR(64), -- SHA-256 для дедупликации
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_images_good ON product_images(good_id);

-- ============================================
-- СЕРТИФИКАТЫ
-- ============================================

CREATE TABLE IF NOT EXISTS certificates (
    id BIGSERIAL PRIMARY KEY,
    good_id INTEGER REFERENCES products(good_id) ON DELETE SET NULL,
    gtin VARCHAR(14),
    rd_number VARCHAR(100) NOT NULL,
    rd_date DATE,
    rd_type VARCHAR(20) CHECK (rd_type IN ('certificate', 'declaration', 'sgr')),
    attr_id INTEGER,
    status VARCHAR(50),
    status_group VARCHAR(50),
    from_date DATE,
    to_date DATE,
    applicant TEXT,
    manufacturer TEXT,
    product_tnved VARCHAR(20),
    product_tech_regulations TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uniq_certificate UNIQUE (rd_number, rd_date)
);

CREATE INDEX idx_cert_good ON certificates(good_id);
CREATE INDEX idx_cert_gtin ON certificates(gtin);
CREATE INDEX idx_cert_number ON certificates(rd_number);

-- ============================================
-- ЛОГ СИНХРОНИЗАЦИИ
-- ============================================

CREATE TABLE IF NOT EXISTS sync_log (
    id BIGSERIAL PRIMARY KEY,
    sync_type VARCHAR(50) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) CHECK (status IN ('running', 'success', 'error')),
    records_processed INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE INDEX idx_sync_log_started ON sync_log(started_at);

-- ============================================
-- TRIGGER ДЛЯ updated_at
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_categories_updated_at BEFORE UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_brands_updated_at BEFORE UPDATE ON brands
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_attribute_models_updated_at BEFORE UPDATE ON attribute_models
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

---

## Шаг 3. Что можно удалить или заменить

### Удалить безболезненно:

1. **Функция `get_good_ids_batch` в `load_attributes.py`** — заменить на более эффективную выборку с фильтрацией по etag.
2. **Проверка структуры ответа в `fetch_product_details`** (строки 90-140) — слишком многословная, упростить до 5-7 строк.
3. **Хардкод дат в `main()`** — вынести в конфигурацию или CLI аргументы.

### Заменить:

1. **`CatalogDatabase` класс** — разделить на репозитории: `ProductsRepository`, `CategoriesRepository`, etc.
2. **Прямые SQL-запросы в `save_product_details`** — использовать ORM (SQLAlchemy) или query builder.
3. **`time.sleep(REQUEST_DELAY)`** — заменить на динамическую задержку на основе заголовков `API-Usage-Limit`.

---

## Резюме: Что сделать прямо сегодня (5-7 пунктов)

1. **Исправить пагинацию в `catalog_client.py`** — добавить цикл с `offset` для загрузки всех товаров из `product-list`.
2. **Добавить нарезку по датам** — разбить диапазон 2020-2026 на кварталы/месяцы для соблюдения лимита 10 000 записей.
3. **Сохранять ETag в БД** — добавить поле `etag` в таблицу `products` и сохранять его при загрузке.
4. **Использовать ETag в запросах** — передавать `If-None-Match` в заголовке для `/v3/feed-product`.
5. **Добавить retry на 5xx** — обернуть запросы в декоратор с экспоненциальным backoff.
6. **Расширить схему БД** — выполнить миграцию из раздела 2 (таблицы categories, brands, countries, product_packages, certificates).
7. **Обработать `set_gtins`** — сохранить вложения наборов в новую таблицу `product_set_items`.
