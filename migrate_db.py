#!/usr/bin/env python3
"""
Миграция базы данных для расширения структуры таблиц
под хранение полной информации о товарах из Национального Каталога
"""

import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv('DB_PATH', 'catalog.db')


def migrate_db():
    """Выполняет миграцию базы данных, добавляя новые таблицы и поля"""
    
    if not os.path.exists(DB_PATH):
        print(f"❌ База данных {DB_PATH} не найдена. Сначала запустите catalog_client.py")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        print("🔄 Начинаем миграцию базы данных...")
        
        # 1. Расширяем таблицу products новыми полями
        print("📝 Расширяем таблицу products...")
        
        alter_queries = [
            "ALTER TABLE products ADD COLUMN is_sim BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN is_kit BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN is_set BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN good_img TEXT",
            "ALTER TABLE products ADD COLUMN good_signed BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN good_mark_flag BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN good_turn_flag BOOLEAN DEFAULT 0",
            "ALTER TABLE products ADD COLUMN create_date TEXT",
            "ALTER TABLE products ADD COLUMN update_date TEXT",
            "ALTER TABLE products ADD COLUMN first_sign_date TEXT",
            "ALTER TABLE products ADD COLUMN producer_inn TEXT",
            "ALTER TABLE products ADD COLUMN producer_name TEXT",
            "ALTER TABLE products ADD COLUMN brand_id INTEGER",
        ]
        
        for query in alter_queries:
            try:
                cursor.execute(query)
                print(f"   ✅ Выполнено: {query.split('ADD COLUMN')[1].split()[0]}")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    print(f"   ⚠️  Поле уже существует: {query.split('ADD COLUMN')[1].split()[0]}")
                else:
                    raise
        
        # 2. Создаём таблицу product_categories
        print("\n📝 Создаём таблицу product_categories...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                good_id INTEGER NOT NULL,
                cat_id INTEGER NOT NULL,
                cat_name TEXT,
                FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_categories_good_id ON product_categories(good_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_categories_cat_id ON product_categories(cat_id)")
        print("   ✅ Таблица product_categories создана")
        
        # 3. Создаём таблицу product_attributes
        print("\n📝 Создаём таблицу product_attributes...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                good_id INTEGER NOT NULL,
                attr_id INTEGER NOT NULL,
                attr_name TEXT NOT NULL,
                attr_value TEXT,
                attr_type TEXT,
                unit TEXT,
                is_required BOOLEAN DEFAULT 0,
                first_layer BOOLEAN DEFAULT 0,
                FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_attributes_good_id ON product_attributes(good_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_attributes_attr_id ON product_attributes(attr_id)")
        print("   ✅ Таблица product_attributes создана")
        
        # 4. Создаём таблицу product_images
        print("\n📝 Создаём таблицу product_images...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                good_id INTEGER NOT NULL,
                photo_type TEXT,
                image_url TEXT,
                FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_good_id ON product_images(good_id)")
        print("   ✅ Таблица product_images создана")
        
        # 5. Создаём таблицу product_identifiers
        print("\n📝 Создаём таблицу product_identifiers...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_identifiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                good_id INTEGER NOT NULL,
                identifier_type TEXT NOT NULL,
                identifier_value TEXT NOT NULL,
                packaging_level INTEGER,
                FOREIGN KEY (good_id) REFERENCES products(good_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_identifiers_good_id ON product_identifiers(good_id)")
        print("   ✅ Таблица product_identifiers создана")
        
        conn.commit()
        
        # 6. Выводим статистику
        cursor.execute("SELECT COUNT(*) FROM products")
        products_count = cursor.fetchone()[0]
        
        print(f"\n{'='*50}")
        print("✅ Миграция успешно завершена!")
        print(f"{'='*50}")
        print(f"📊 В базе данных {products_count} товаров")
        print(f"📁 Файл БД: {DB_PATH}")
        print(f"\n📋 Созданные таблицы:")
        print("   • products (расширена)")
        print("   • product_categories")
        print("   • product_attributes")
        print("   • product_images")
        print("   • product_identifiers")
        
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Ошибка миграции: {e}")
        return False
        
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_db()
