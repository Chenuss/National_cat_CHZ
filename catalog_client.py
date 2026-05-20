#!/usr/bin/env python3
"""
Клиент для работы с API Национального Каталога маркированных товаров (Честный Знак).

Этот скрипт получает список товаров через API и сохраняет их в базу данных SQLite.
"""

import os
import sqlite3
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NationalCatalogClient:
    """Клиент для работы с API Национального Каталога."""
    
    def __init__(self, api_key: Optional[str] = None, api_url: Optional[str] = None):
        """
        Инициализация клиента.
        
        Args:
            api_key: Ключ доступа API. Если не указан, берётся из переменной окружения API_KEY.
            api_url: URL API. Если не указан, берётся из переменной окружения API_URL.
        """
        self.api_key = api_key or os.getenv('API_KEY')
        self.api_url = api_url or os.getenv('API_URL', 'https://api.nk.sandbox.crptech.ru')
        
        if not self.api_key:
            raise ValueError("API ключ не найден. Укажите его в параметре или в переменной окружения API_KEY.")
        
        self.base_url = f"{self.api_url}/v4"
        self.session = requests.Session()
        self.session.params = {'apikey': self.api_key}
        
        logger.info(f"Инициализирован клиент для API: {self.api_url}")
    
    def get_product_list(
        self,
        from_date: str = "2020-01-01 00:00:00",
        to_date: str = "2026-12-31 23:59:59",
        limit: int = 1000,
        good_status: str = "published"
    ) -> List[Dict[str, Any]]:
        """
        Получение списка товаров из Национального Каталога.
        
        Args:
            from_date: Дата начала периода (формат: YYYY-MM-DD HH:MM:SS).
            to_date: Дата окончания периода (формат: YYYY-MM-DD HH:MM:SS).
            limit: Количество записей за один запрос (максимум 1000).
            good_status: Статус карточки товара.
            
        Returns:
            Список словарей с данными о товарах.
        """
        endpoint = f"{self.base_url}/product-list"
        
        params = {
            'from_date': from_date,
            'to_date': to_date,
            'limit': limit,
            'good_status': good_status
        }
        
        logger.info(f"Выполнение запроса к {endpoint}")
        logger.info(f"Параметры: {params}")
        
        try:
            response = self.session.get(endpoint, params=params)
            response.raise_for_status()
            
            # Проверяем лимиты API
            api_limit = response.headers.get('API-Usage-Limit', 'N/A')
            logger.info(f"Использование API: {api_limit}")
            
            data = response.json()
            
            # API может возвращать данные в разных форматах
            if isinstance(data, list):
                products = data
            elif isinstance(data, dict) and 'data' in data:
                products = data['data']
            else:
                products = [data] if data else []
            
            logger.info(f"Получено {len(products)} товаров")
            return products
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при выполнении запроса: {e}")
            raise


class CatalogDatabase:
    """Класс для работы с базой данных каталога."""
    
    def __init__(self, db_path: str = "catalog.db"):
        """
        Инициализация подключения к базе данных.
        
        Args:
            db_path: Путь к файлу базы данных SQLite.
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        self._create_tables()
        logger.info(f"Подключено к базе данных: {db_path}")
    
    def _create_tables(self):
        """Создание таблиц базы данных."""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS products (
            good_id INTEGER PRIMARY KEY,
            gtin TEXT,
            good_name TEXT,
            tnved TEXT,
            brand_name TEXT,
            good_status TEXT,
            good_detailed_status TEXT,
            updated_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
        CREATE INDEX IF NOT EXISTS idx_products_good_status ON products(good_status);
        CREATE INDEX IF NOT EXISTS idx_products_brand_name ON products(brand_name);
        """
        
        self.cursor.executescript(create_table_sql)
        self.conn.commit()
        logger.info("Таблицы базы данных созданы/проверены")
    
    def insert_or_update_products(self, products: List[Dict[str, Any]]) -> int:
        """
        Вставка или обновление товаров в базе данных.
        
        Args:
            products: Список словарей с данными о товарах.
            
        Returns:
            Количество обработанных записей.
        """
        count = 0
        
        for product in products:
            try:
                # Извлекаем данные из ответа API
                good_id = product.get('good_id')
                gtin = product.get('gtin', '')
                good_name = product.get('good_name', '')
                tnved = product.get('tnved', '')
                brand_name = product.get('brand_name', '')
                good_status = product.get('good_status', '')
                
                # good_detailed_status может быть списком, преобразуем в JSON строку
                good_detailed_status = product.get('good_detailed_status', [])
                if isinstance(good_detailed_status, list):
                    good_detailed_status = json.dumps(good_detailed_status, ensure_ascii=False)
                
                # updated_date может быть в разных форматах, берём как есть
                updated_date = product.get('updated_date', '')
                
                if not good_id:
                    logger.warning(f"Пропущена запись без good_id: {product}")
                    continue
                
                # SQL запрос для вставки или обновления (UPSERT)
                upsert_sql = """
                INSERT INTO products (
                    good_id, gtin, good_name, tnved, brand_name,
                    good_status, good_detailed_status, updated_date, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(good_id) DO UPDATE SET
                    gtin = excluded.gtin,
                    good_name = excluded.good_name,
                    tnved = excluded.tnved,
                    brand_name = excluded.brand_name,
                    good_status = excluded.good_status,
                    good_detailed_status = excluded.good_detailed_status,
                    updated_date = excluded.updated_date,
                    updated_at = CURRENT_TIMESTAMP;
                """
                
                self.cursor.execute(upsert_sql, (
                    good_id, gtin, good_name, tnved, brand_name,
                    good_status, good_detailed_status, updated_date
                ))
                
                count += 1
                
            except Exception as e:
                logger.error(f"Ошибка при обработке товара {product.get('good_id', 'N/A')}: {e}")
                continue
        
        self.conn.commit()
        logger.info(f"Обработано {count} записей")
        return count
    
    def get_products_count(self) -> int:
        """Получение общего количества товаров в базе данных."""
        self.cursor.execute("SELECT COUNT(*) FROM products")
        return self.cursor.fetchone()[0]
    
    def close(self):
        """Закрытие подключения к базе данных."""
        if self.conn:
            self.conn.close()
            logger.info("Подключение к базе данных закрыто")


def main():
    """Основная функция."""
    logger.info("=" * 60)
    logger.info("Начало работы с Национальным Каталогом")
    logger.info("=" * 60)
    
    try:
        # Инициализация клиента API
        client = NationalCatalogClient()
        
        # Инициализация базы данных
        db = CatalogDatabase("catalog.db")
        
        # Получение списка товаров
        products = client.get_product_list(
            from_date="2020-01-01 00:00:00",
            to_date="2026-12-31 23:59:59",
            limit=1000,
            good_status="published"
        )
        
        if products:
            # Сохранение в базу данных
            count = db.insert_or_update_products(products)
            total = db.get_products_count()
            
            logger.info("=" * 60)
            logger.info(f"Успешно сохранено {count} товаров")
            logger.info(f"Всего товаров в базе данных: {total}")
            logger.info("=" * 60)
            
            # Вывод примера первого товара
            if products:
                logger.info("\nПример первого товара:")
                logger.info(json.dumps(products[0], indent=2, ensure_ascii=False))
        else:
            logger.info("Товары не найдены")
        
        db.close()
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise


if __name__ == "__main__":
    main()
