-- Схема базы данных для Национального Каталога (SQLite)
-- Версия: 1.0 (Фаза 1 - рефакторинг API-клиента и схемы БД)
-- Кодировка: UTF-8 (поддержка кириллицы, китайских иероглифов, спецсимволов)

-- ============================================================================
-- ТАБЛИЦА: products - Основная таблица товаров
-- ============================================================================
-- Хранит базовую информацию о товарах из Национального Каталога.
-- good_id является первичным ключом для обеспечения идемпотентности.

CREATE TABLE IF NOT EXISTS products (
    -- Идентификатор товара в Национальном Каталоге (первичный ключ)
    good_id INTEGER PRIMARY KEY,
    
    -- Глобальный штрих-код товара (до 14 символов)
    gtin VARCHAR(14) NOT NULL,
    
    -- Наименование товара
    good_name TEXT NOT NULL,
    
    -- Код ТН ВЭД (товарной номенклатуры внешнеэкономической деятельности)
    tnved VARCHAR(20),
    
    -- Наименование бренда/товарного знака
    brand_name TEXT,
    
    -- Идентификатор бренда в справочнике НК
    brand_id INTEGER,
    
    -- Технологический статус карточки (draft/published/archived и т.д.)
    good_status VARCHAR(50),
    
    -- Массив детальных статусов карточки (хранится как JSON)
    -- Пример: ["draft", "moderation", "errors"]
    good_detailed_status JSONB,
    
    -- Признак карточки типа "Комплект" (true/false)
    is_kit BOOLEAN DEFAULT FALSE,
    
    -- Признак карточки типа "Набор" (true/false)
    is_set BOOLEAN DEFAULT FALSE,
    
    -- Признак карточки индустриальной маркировки (префикс 004)
    is_sim BOOLEAN DEFAULT FALSE,
    
    -- Признак технического GTIN (префикс 029)
    is_tech_gtin BOOLEAN DEFAULT FALSE,
    
    -- Признак подписания карточки товара
    good_signed BOOLEAN,
    
    -- Признак заполнения атрибутов первого слоя (возможность эмиссии КМ)
    good_mark_flag BOOLEAN,
    
    -- Признак заполнения атрибутов второго слоя (возможность ввода в оборот)
    good_turn_flag BOOLEAN,
    
    -- ИНН производителя/импортера
    producer_inn VARCHAR(12),
    
    -- Наименование производителя/импортера
    producer_name TEXT,
    
    -- Дата создания карточки
    create_date TIMESTAMP,
    
    -- Дата обновления карточки
    update_date TIMESTAMP,
    
    -- Дата первого подписания карточки
    first_sign_date TIMESTAMP,
    
    -- Дата обновления флагов
    flags_updated_date TIMESTAMP,
    
    -- URL фотографии по умолчанию
    good_img TEXT,
    
    -- ETag для инкрементального обновления (методы: product, short-product)
    etag VARCHAR(100),
    
    -- Дата и время создания записи в локальной БД
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Дата и время последнего обновления записи в локальной БД
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для ускорения поиска
CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
CREATE INDEX IF NOT EXISTS idx_products_good_status ON products(good_status);
CREATE INDEX IF NOT EXISTS idx_products_brand_name ON products(brand_name);
CREATE INDEX IF NOT EXISTS idx_products_producer_inn ON products(producer_inn);
CREATE INDEX IF NOT EXISTS idx_products_is_set ON products(is_set);
CREATE INDEX IF NOT EXISTS idx_products_is_kit ON products(is_kit);
CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at);

-- ============================================================================
-- ТАБЛИЦА: product_packages - Уровни упаковки товаров
-- ============================================================================
-- Хранит информацию об идентификаторах и уровнях упаковки.
-- Один товар может иметь несколько упаковок разных уровней.

CREATE TABLE IF NOT EXISTS product_packages (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Ссылка на товар (foreign key к products.good_id)
    good_id INTEGER NOT NULL,
    
    -- Тип идентификатора (gtin, sku, и т.д.)
    identifier_type VARCHAR(50) NOT NULL,
    
    -- Значение идентификатора (например, GTIN упаковки)
    identifier_value VARCHAR(100) NOT NULL,
    
    -- Уровень упаковки
    -- Возможные значения: trade-unit, inner-pack, box, layer, pallet, metro-unit, show-pack
    level VARCHAR(50) NOT NULL,
    
    -- Количество товаров в упаковке (множитель)
    multiplier INTEGER DEFAULT 1,
    
    -- Ширина упаковки (мм)
    width_mm INTEGER,
    
    -- Высота упаковки (мм)
    height_mm INTEGER,
    
    -- Длина упаковки (мм)
    length_mm INTEGER,
    
    -- Вес брутто упаковки (г)
    weight_gross_g INTEGER,
    
    -- Вес нетто упаковки (г)
    weight_net_g INTEGER,
    
    -- Материал упаковки (справочное значение)
    material VARCHAR(100),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Ограничение уникальности: на одном уровне может быть несколько упаковок
    UNIQUE(good_id, level, identifier_value),
    
    -- Внешний ключ
    FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_packages_good_id ON product_packages(good_id);
CREATE INDEX IF NOT EXISTS idx_product_packages_level ON product_packages(level);
CREATE INDEX IF NOT EXISTS idx_product_packages_identifier ON product_packages(identifier_value);

-- ============================================================================
-- ТАБЛИЦА: product_attributes - Атрибуты товаров (EAV-модель)
-- ============================================================================
-- Entity-Attribute-Value модель для хранения динамических атрибутов товаров.
-- Позволяет хранить произвольное количество атрибутов для каждого товара.

CREATE TABLE IF NOT EXISTS product_attributes (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Ссылка на товар
    good_id INTEGER NOT NULL,
    
    -- Идентификатор атрибута в справочнике НК
    attr_id INTEGER NOT NULL,
    
    -- Наименование атрибута (кэшируется из справочника)
    attr_name TEXT,
    
    -- Значение атрибута (может быть строкой, числом, JSON для составных значений)
    attr_value TEXT,
    
    -- Тип значения атрибута (string, integer, decimal, boolean, list, file)
    value_type VARCHAR(20),
    
    -- Единица измерения (если применимо)
    unit VARCHAR(50),
    
    -- Признак обязательности атрибута (true - обязательный, false - опциональный)
    is_required BOOLEAN DEFAULT FALSE,
    
    -- Признак мультиплицируемости (можно ли указывать несколько значений)
    is_multiplicable BOOLEAN DEFAULT FALSE,
    
    -- Слой атрибута (first_layer / second_layer)
    layer VARCHAR(20),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Уникальность пары товар-атрибут (для не-мультиплицируемых атрибутов)
    UNIQUE(good_id, attr_id),
    
    -- Внешний ключ
    FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_attributes_good_id ON product_attributes(good_id);
CREATE INDEX IF NOT EXISTS idx_product_attributes_attr_id ON product_attributes(attr_id);
CREATE INDEX IF NOT EXISTS idx_product_attributes_value ON product_attributes(attr_value);

-- ============================================================================
-- ТАБЛИЦА: product_images - Изображения товаров
-- ============================================================================
-- Хранит информацию об изображениях товаров.

CREATE TABLE IF NOT EXISTS product_images (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Ссылка на товар
    good_id INTEGER NOT NULL,
    
    -- Тип изображения (main, additional, pack, и т.д.)
    photo_type VARCHAR(50),
    
    -- URL изображения в Национальном Каталоге
    photo_url TEXT NOT NULL,
    
    -- Локальный путь к скачанному изображению (если загружено)
    local_path TEXT,
    
    -- Размер изображения (small, medium, large, original)
    size VARCHAR(20),
    
    -- Ширина изображения (пиксели)
    width_px INTEGER,
    
    -- Высота изображения (пиксели)
    height_px INTEGER,
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Внешний ключ
    FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_images_good_id ON product_images(good_id);
CREATE INDEX IF NOT EXISTS idx_product_images_photo_type ON product_images(photo_type);

-- ============================================================================
-- ТАБЛИЦА: certificates - Разрешительные документы
-- ============================================================================
-- Хранит информацию о сертификатах, декларациях, СГР на товары.

CREATE TABLE IF NOT EXISTS certificates (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Ссылка на товар
    good_id INTEGER NOT NULL,
    
    -- GTIN товара (для связи при загрузке по GTIN)
    gtin VARCHAR(14),
    
    -- Идентификатор типа документа в НК
    -- 23561 - Сертификат соответствия
    -- 23557 - Декларация о соответствии
    -- 23765 - Свидетельство о государственной регистрации (СГР)
    -- 23555 - Протокол испытаний
    -- 23890 - Регистрационное удостоверение
    attr_id INTEGER NOT NULL CHECK (attr_id IN (23561, 23557, 23765, 23555, 23890)),
    
    -- Номер разрешительного документа
    number VARCHAR(100) NOT NULL,
    
    -- Дата выдачи документа
    from_date DATE,
    
    -- Дата окончания действия документа
    to_date DATE,
    
    -- Статус документа (active, expired, cancelled, и т.д.)
    status VARCHAR(50),
    
    -- Группа статуса документа
    status_group VARCHAR(50),
    
    -- ТН ВЭД продукта, на который выдан документ
    product_tnved VARCHAR(20),
    
    -- Заявитель (наименование организации)
    applicant TEXT,
    
    -- Производитель (наименование организации)
    manufacturer TEXT,
    
    -- Технические регламенты, которым соответствует продукт
    product_tech_regulations TEXT,
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Внешний ключ
    FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_certificates_good_id ON certificates(good_id);
CREATE INDEX IF NOT EXISTS idx_certificates_gtin ON certificates(gtin);
CREATE INDEX IF NOT EXISTS idx_certificates_number ON certificates(number);
CREATE INDEX IF NOT EXISTS idx_certificates_status ON certificates(status);

-- ============================================================================
-- ТАБЛИЦА: product_set_items - Элементы наборов (is_set=true)
-- ============================================================================
-- Хранит информацию о вложенных товарах в наборах.

CREATE TABLE IF NOT EXISTS product_set_items (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Ссылка на родительский товар (набор)
    parent_good_id INTEGER NOT NULL,
    
    -- GTIN вложенного товара
    item_gtin VARCHAR(14) NOT NULL,
    
    -- Количество вложенных товаров в наборе
    quantity INTEGER DEFAULT 1,
    
    -- Ссылка на вложенный товар (если он есть в локальной БД)
    item_good_id INTEGER,
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Уникальность элемента в наборе
    UNIQUE(parent_good_id, item_gtin),
    
    -- Внешние ключи
    FOREIGN KEY (parent_good_id) REFERENCES products(good_id) ON DELETE CASCADE,
    FOREIGN KEY (item_good_id) REFERENCES products(good_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_product_set_items_parent ON product_set_items(parent_good_id);
CREATE INDEX IF NOT EXISTS idx_product_set_items_item_gtin ON product_set_items(item_gtin);

-- ============================================================================
-- ТАБЛИЦА: categories - Справочник категорий
-- ============================================================================
-- Дерево категорий товаров из Национального Каталога.

CREATE TABLE IF NOT EXISTS categories (
    -- Идентификатор категории (первичный ключ)
    cat_id INTEGER PRIMARY KEY,
    
    -- Наименование категории
    cat_name TEXT NOT NULL,
    
    -- Идентификатор родительской категории (NULL для корневых)
    cat_parent_id INTEGER,
    
    -- Уровень вложенности в дереве категорий (1 = корень)
    cat_level INTEGER DEFAULT 1,
    
    -- Признак активности категории
    category_active BOOLEAN DEFAULT TRUE,
    
    -- Коды товарных групп ГИС МТ (JSON массив)
    gismt_codes JSONB,
    
    -- Коды ТН ВЭД, связанные с категорией (JSON массив)
    tnved_codes JSONB,
    
    -- ETag для инкрементального обновления справочника
    etag VARCHAR(100),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Дата обновления записи
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Внешний ключ на родителя
    FOREIGN KEY (cat_parent_id) REFERENCES categories(cat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(cat_parent_id);
CREATE INDEX IF NOT EXISTS idx_categories_level ON categories(cat_level);
CREATE INDEX IF NOT EXISTS idx_categories_active ON categories(category_active);

-- ============================================================================
-- ТАБЛИЦА: brands - Справочник брендов
-- ============================================================================
-- Справочник товарных знаков/брендов из Национального Каталога.

CREATE TABLE IF NOT EXISTS brands (
    -- Идентификатор бренда (первичный ключ)
    brand_id INTEGER PRIMARY KEY,
    
    -- Наименование бренда
    brand_name TEXT NOT NULL,
    
    -- Полное юридическое наименование владельца бренда
    owner_name TEXT,
    
    -- ИНН владельца бренда
    owner_inn VARCHAR(12),
    
    -- Страна происхождения бренда (ISO Alpha-2 код)
    country_code VARCHAR(2),
    
    -- ETag для инкрементального обновления справочника
    etag VARCHAR(100),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Дата обновления записи
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(brand_name);
CREATE INDEX IF NOT EXISTS idx_brands_owner_inn ON brands(owner_inn);
CREATE INDEX IF NOT EXISTS idx_brands_country ON brands(country_code);

-- ============================================================================
-- ТАБЛИЦА: countries - Справочник стран (ISO 3166-1 Alpha-2)
-- ============================================================================
-- Справочник стран производства.

CREATE TABLE IF NOT EXISTS countries (
    -- ISO Alpha-2 код страны (первичный ключ)
    code VARCHAR(2) PRIMARY KEY,
    
    -- Полное наименование страны на русском языке
    name_ru TEXT NOT NULL,
    
    -- Полное наименование страны на английском языке
    name_en TEXT,
    
    -- ETag для инкрементального обновления справочника
    etag VARCHAR(100),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Дата обновления записи
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- ТАБЛИЦА: attribute_models - Атрибутивные модели категорий
-- ============================================================================
-- Хранит структуру атрибутов для каждой категории/ТН ВЭД.

CREATE TABLE IF NOT EXISTS attribute_models (
    -- Первичный ключ записи
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Идентификатор категории, для которой определён атрибут
    cat_id INTEGER,
    
    -- Код ТН ВЭД, для которого определён атрибут
    tnved VARCHAR(20),
    
    -- Идентификатор атрибута
    attr_id INTEGER NOT NULL,
    
    -- Наименование атрибута
    attr_name TEXT NOT NULL,
    
    -- Тип поля атрибута (string, integer, decimal, boolean, list, file, и т.д.)
    attr_field_type VARCHAR(50),
    
    -- Обязательность атрибута (required/recommended/optional)
    requirement VARCHAR(20),
    
    -- Признак мультиплицируемости (можно ли несколько значений)
    is_multiplicable BOOLEAN DEFAULT FALSE,
    
    -- Слой атрибута (first_layer/second_layer)
    layer VARCHAR(20),
    
    -- URL пресета значений (если есть)
    preset_url TEXT,
    
    -- ETag для инкрементального обновления справочника
    etag VARCHAR(100),
    
    -- Дата создания записи
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Дата обновления записи
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Уникальность связки категория-атрибут или ТН ВЭД-атрибут
    UNIQUE(cat_id, attr_id),
    UNIQUE(tnved, attr_id)
);

CREATE INDEX IF NOT EXISTS idx_attribute_models_cat_id ON attribute_models(cat_id);
CREATE INDEX IF NOT EXISTS idx_attribute_models_tnved ON attribute_models(tnved);
CREATE INDEX IF NOT EXISTS idx_attribute_models_attr_id ON attribute_models(attr_id);

-- ============================================================================
-- ТАБЛИЦА: sync_state - Курсоры синхронизации
-- ============================================================================
-- Хранит состояние синхронизации: последние offset'ы, даты среза, ETag справочников.

CREATE TABLE IF NOT EXISTS sync_state (
    -- Ключ состояния (уникальный идентификатор)
    -- Примеры: 'product_list_offset', 'last_sync_date', 'categories_etag'
    key VARCHAR(100) PRIMARY KEY,
    
    -- Значение состояния (хранится как JSON для гибкости)
    -- Примеры: числовое значение, дата, объект с метаданными
    value JSONB NOT NULL,
    
    -- Дата и время последнего обновления состояния
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- ПРЕДСТАВЛЕНИЯ (VIEWS) для удобной работы с данными
-- ============================================================================

-- Представление: товары с полным количеством упаковок
CREATE VIEW IF NOT EXISTS v_products_with_packages AS
SELECT 
    p.good_id,
    p.gtin,
    p.good_name,
    p.brand_name,
    p.good_status,
    p.is_set,
    p.is_kit,
    COUNT(DISTINCT pp.id) as package_count,
    GROUP_CONCAT(DISTINCT pp.level) as package_levels
FROM products p
LEFT JOIN product_packages pp ON p.good_id = pp.good_id
GROUP BY p.good_id, p.gtin, p.good_name, p.brand_name, p.good_status, p.is_set, p.is_kit;

-- Представление: товары с их атрибутами в сводном виде
CREATE VIEW IF NOT EXISTS v_products_with_attrs AS
SELECT 
    p.good_id,
    p.gtin,
    p.good_name,
    GROUP_CONCAT(pa.attr_name || ': ' || pa.attr_value, '; ') as attributes_summary
FROM products p
LEFT JOIN product_attributes pa ON p.good_id = pa.good_id
GROUP BY p.good_id, p.gtin, p.good_name;

-- Представление: наборы с их составом
CREATE VIEW IF NOT EXISTS v_sets_with_items AS
SELECT 
    p.good_id,
    p.gtin,
    p.good_name,
    psi.item_gtin,
    psi.quantity,
    ip.good_name as item_name
FROM products p
JOIN product_set_items psi ON p.good_id = psi.parent_good_id
LEFT JOIN products ip ON psi.item_good_id = ip.good_id
WHERE p.is_set = TRUE;

-- ============================================================================
-- ПРИМЕЧАНИЯ ПО ИСПОЛЬЗОВАНИЮ
-- ============================================================================
-- 
-- 1. Для SQLite тип JSONB эмулируется через TEXT. При использовании PostgreSQL
--    замените JSONB на нативный тип JSONB.
--
-- 2. Поле good_detailed_status хранится как JSON-строка в SQLite.
--    Для парсинга используйте json_extract() (SQLite 3.38+) или обрабатывайте
--    в Python коде.
--
-- 3. Для идемпотентности используйте INSERT OR REPLACE (SQLite) или
--    INSERT ... ON CONFLICT DO UPDATE (PostgreSQL).
--
-- 4. ETag сохраняется только для методов: product, short-product, categories, brands.
--    Метод feed-product не поддерживает ETag согласно документации v.5.59.
--
-- ============================================================================
