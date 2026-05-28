-- Миграция для Фазы 3: Добавление полей для синхронизации товаров
-- Выполнить: sqlite3 catalog.db < migrations/add_product_fields.sql

-- Таблица products
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS last_sync_at DATETIME;
ALTER TABLE products ADD COLUMN IF NOT EXISTS etag VARCHAR(255);
ALTER TABLE products ADD COLUMN IF NOT EXISTS good_detailed_status JSON;
ALTER TABLE products ADD COLUMN IF NOT EXISTS remainder_type VARCHAR(50);

-- Таблица product_attributes (расширение для сертификатов и мультипликаторов)
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS level VARCHAR(50);
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS gtin VARCHAR(14);
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS multiplier INTEGER;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS attr_group_id INTEGER;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS attr_group_name VARCHAR(200);
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS attr_value_id INTEGER;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS attr_value_type VARCHAR(50);
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_number VARCHAR(255);
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_issued_date DATETIME;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_valid_until_date DATETIME;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_applicant TEXT;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_manufacturer TEXT;
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS certificate_product_description TEXT;

-- Таблица product_packages (весогабариты и детали)
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS gtin VARCHAR(14);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS height VARCHAR(50);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS depth VARCHAR(50);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS width VARCHAR(50);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS weight_gross VARCHAR(50);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS volume VARCHAR(50);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS package_type VARCHAR(100);
ALTER TABLE product_packages ADD COLUMN IF NOT EXISTS material VARCHAR(100);

-- Таблица product_images
ALTER TABLE product_images ADD COLUMN IF NOT EXISTS photo_date DATETIME;
ALTER TABLE product_images ADD COLUMN IF NOT EXISTS barcode VARCHAR(50);

-- Таблица product_set_items
ALTER TABLE product_set_items ADD COLUMN IF NOT EXISTS child_gtin VARCHAR(14);
