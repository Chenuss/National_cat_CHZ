#!/usr/bin/env python3
"""
Загрузка детальной информации о товарах и атрибутов из Национального Каталога
через метод /v3/feed-product
"""

import sqlite3
import requests
import os
import time
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()

# Конфигурация
API_URL = os.getenv('API_URL', 'https://апи.национальный-каталог.рф')
API_KEY = os.getenv('API_KEY')
DB_PATH = os.getenv('DB_PATH', 'catalog.db')
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '25'))  # Максимум 25 товаров в запросе
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '1.0'))  # Задержка между запросами в секундах


def get_db_connection() -> sqlite3.Connection:
    """Создаёт подключение к базе данных"""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"База данных {DB_PATH} не найдена")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_good_ids_batch(conn: sqlite3.Connection, batch_size: int, offset: int = 0) -> List[int]:
    """Получает пачку good_id из базы данных"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT good_id FROM products LIMIT ? OFFSET ?",
        (batch_size, offset)
    )
    return [row['good_id'] for row in cursor.fetchall()]


def get_all_good_ids_count(conn: sqlite3.Connection) -> int:
    """Получает общее количество товаров в БД"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM products")
    return cursor.fetchone()['count']


def fetch_product_details(good_ids: List[int]) -> Dict[str, Any]:
    """
    Запрашивает детальную информацию о товарах через API
    
    Args:
        good_ids: Список ID товаров (максимум 25)
    
    Returns:
        Словарь с данными о товарах или None при ошибке
    """
    if not good_ids:
        return None
    
    url = f"{API_URL}/v3/feed-product"
    params = {
        'good_ids': ';'.join(map(str, good_ids)),
        'apikey': API_KEY
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        
        # Обработка ошибок
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            print(f"   ⏳ Лимит запросов превышен. Ждём {retry_after} секунд...")
            time.sleep(retry_after)
            return fetch_product_details(good_ids)  # Повторный запрос
        
        if response.status_code == 304:
            print("   ℹ️  Данные не изменились (ETag)")
            return None
        
        if response.status_code != 200:
            print(f"   ❌ Ошибка API: {response.status_code} - {response.text[:200]}")
            return None
        
        data = response.json()
        
        # Подробное логирование структуры ответа
        print(f"   📄 Получен ответ. Ключи верхнего уровня: {list(data.keys())}")
        
        # Проверка структуры ответа
        if 'result' not in data:
            print(f"   ❌ Неверная структура ответа: отсутствует ключ 'result'")
            print(f"   🔍 Полная структура ответа: {data}")
            return None
        
        if 'goods' not in data['result']:
            print(f"   ❌ Неверная структура ответа: отсутствует ключ 'goods' в 'result'")
            print(f"   🔍 Ключи в result: {list(data['result'].keys())}")
            print(f"   🔍 Полная структура ответа: {data}")
            return None
        
        goods = data['result']['goods']
        print(f"   ✅ Найдено товаров в ответе: {len(goods) if isinstance(goods, list) else 'N/A'}")
        
        # Логгируем структуру первого товара для отладки
        if isinstance(goods, list) and len(goods) > 0:
            first_good = goods[0]
            print(f"   📋 Структура первого товара: ключи = {list(first_good.keys()) if isinstance(first_good, dict) else type(first_good)}")
        
        return goods
        
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Ошибка запроса: {e}")
        return None
    except Exception as e:
        print(f"   ❌ Ошибка парсинга: {e}")
        return None


def save_product_details(conn: sqlite3.Connection, products: List[Dict[str, Any]]) -> None:
    """Сохраняет детальную информацию о товарах в базу данных"""
    cursor = conn.cursor()
    
    for product in products:
        good_id = product.get('good_id')
        
        if not good_id:
            continue
        
        # 1. Обновляем таблицу products
        update_query = """
            UPDATE products SET
                is_sim = ?,
                is_kit = ?,
                is_set = ?,
                good_img = ?,
                good_signed = ?,
                good_mark_flag = ?,
                good_turn_flag = ?,
                create_date = ?,
                update_date = ?,
                first_sign_date = ?,
                producer_inn = ?,
                producer_name = ?,
                brand_id = ?
            WHERE good_id = ?
        """
        
        cursor.execute(update_query, (
            product.get('is_sim'),
            product.get('is_kit'),
            product.get('is_set'),
            product.get('good_img'),
            product.get('good_signed'),
            product.get('good_mark_flag'),
            product.get('good_turn_flag'),
            product.get('create_date'),
            product.get('update_date'),
            product.get('first_sign_date'),
            product.get('producer_inn'),
            product.get('producer_name'),
            product.get('brand_id'),
            good_id
        ))
        
        # 2. Сохраняем категории
        cursor.execute("DELETE FROM product_categories WHERE good_id = ?", (good_id,))
        categories = product.get('categories', [])
        if categories:
            for cat in categories:
                cursor.execute(
                    "INSERT INTO product_categories (good_id, cat_id, cat_name) VALUES (?, ?, ?)",
                    (good_id, cat.get('cat_id'), cat.get('cat_name'))
                )
        
        # 3. Сохраняем атрибуты
        cursor.execute("DELETE FROM product_attributes WHERE good_id = ?", (good_id,))
        attributes = product.get('attributes', [])
        if attributes:
            for attr in attributes:
                cursor.execute(
                    """INSERT INTO product_attributes 
                       (good_id, attr_id, attr_name, attr_value, attr_type, unit, is_required, first_layer) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        good_id,
                        attr.get('attr_id'),
                        attr.get('attr_name'),
                        attr.get('attr_value'),
                        attr.get('attr_type'),
                        attr.get('unit'),
                        attr.get('is_required'),
                        attr.get('first_layer')
                    )
                )
        
        # 4. Сохраняем изображения
        cursor.execute("DELETE FROM product_images WHERE good_id = ?", (good_id,))
        images = product.get('good_images', [])
        if images and isinstance(images, list):
            for img in images:
                if isinstance(img, dict):
                    cursor.execute(
                        "INSERT INTO product_images (good_id, photo_type, image_url) VALUES (?, ?, ?)",
                        (good_id, img.get('photo_type'), img.get('href'))
                    )
                elif isinstance(img, str):
                    cursor.execute(
                        "INSERT INTO product_images (good_id, image_url) VALUES (?, ?)",
                        (good_id, img)
                    )
        
        # 5. Сохраняем идентификаторы
        cursor.execute("DELETE FROM product_identifiers WHERE good_id = ?", (good_id,))
        identifiers = product.get('identified_by', [])
        if identifiers:
            for ident in identifiers:
                cursor.execute(
                    """INSERT INTO product_identifiers 
                       (good_id, identifier_type, identifier_value, packaging_level) 
                       VALUES (?, ?, ?, ?)""",
                    (
                        good_id,
                        ident.get('type'),
                        ident.get('value'),
                        ident.get('packaging_level')
                    )
                )
    
    conn.commit()


def load_attributes():
    """Основная функция загрузки атрибутов"""
    
    if not API_KEY:
        print("❌ API_KEY не найден в .env файле")
        return False
    
    try:
        conn = get_db_connection()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return False
    
    try:
        total_count = get_all_good_ids_count(conn)
        print(f"\n{'='*60}")
        print(f"🚀 Загрузка детальной информации о товарах")
        print(f"{'='*60}")
        print(f"📊 Всего товаров в БД: {total_count}")
        print(f"📦 Размер пачки: {BATCH_SIZE}")
        print(f"⏱️  Задержка между запросами: {REQUEST_DELAY} сек")
        print(f"🔗 API URL: {API_URL}")
        print(f"{'='*60}\n")
        
        processed = 0
        success_count = 0
        error_count = 0
        offset = 0
        
        while True:
            # Получаем пачку good_id
            good_ids = get_good_ids_batch(conn, BATCH_SIZE, offset)
            
            if not good_ids:
                break
            
            batch_num = (offset // BATCH_SIZE) + 1
            print(f"📦 Пачка {batch_num}: обрабатываем товары {offset+1}-{offset+len(good_ids)}")
            
            # Запрашиваем данные из API
            products = fetch_product_details(good_ids)
            
            if products:
                save_product_details(conn, products)
                success_count += len(products)
                print(f"   ✅ Загружено {len(products)} товаров")
            else:
                error_count += 1
                print(f"   ⚠️  Ошибка загрузки пачки")
            
            processed += len(good_ids)
            offset += BATCH_SIZE
            
            # Прогресс
            progress = (processed / total_count) * 100
            print(f"   📈 Прогресс: {progress:.1f}% ({processed}/{total_count})\n")
            
            # Задержка между запросами
            if offset < total_count:
                time.sleep(REQUEST_DELAY)
        
        # Итоговая статистика
        print(f"\n{'='*60}")
        print("✅ Загрузка завершена!")
        print(f"{'='*60}")
        print(f"📊 Обработано товаров: {processed}")
        print(f"✅ Успешно загружено: {success_count}")
        print(f"⚠️  Ошибок: {error_count}")
        
        # Статистика по таблицам
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM product_categories")
        cat_count = cursor.fetchone()[0]
        print(f"📁 Категорий сохранено: {cat_count}")
        
        cursor.execute("SELECT COUNT(*) FROM product_attributes")
        attr_count = cursor.fetchone()[0]
        print(f"🏷️  Атрибутов сохранено: {attr_count}")
        
        cursor.execute("SELECT COUNT(*) FROM product_images")
        img_count = cursor.fetchone()[0]
        print(f"🖼️  Изображений сохранено: {img_count}")
        
        cursor.execute("SELECT COUNT(*) FROM product_identifiers")
        ident_count = cursor.fetchone()[0]
        print(f"🆔 Идентификаторов сохранено: {ident_count}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        conn.close()


if __name__ == "__main__":
    load_attributes()
