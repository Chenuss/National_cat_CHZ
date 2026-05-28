#!/usr/bin/env python3
"""
CLI-точка входа для интеграции с Национальным Каталогом.

Использование:
    python main.py load-catalogs              # Загрузить все справочники
    python main.py load-catalogs --only categories  # Только категории
    python main.py load-catalogs --only brands      # Только бренды
    python main.py load-catalogs --only countries   # Только страны
    python main.py load-catalogs --only attributes  # Только атрибуты
"""

import logging
import argparse
import sys
from typing import Optional, List

from sqlalchemy.orm import Session

from config import settings
from nk_client import NKCatalogClient
from models import Base, create_database_engine
from catalogs_loader import CatalogsLoader
from product_sync import ProductSyncManager


# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def create_session() -> Session:
    """Создаёт SQLAlchemy сессию."""
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return Session(bind=engine)


def cmd_load_catalogs(only: Optional[str] = None) -> None:
    """
    Загружает справочники Национального Каталога.
    
    Args:
        only: Если указано, загружает только указанный справочник
    """
    logger.info("Запуск загрузки справочников...")
    
    client = NKCatalogClient()
    session = create_session()
    
    try:
        loader = CatalogsLoader(client, session)
        
        if only:
            # Загрузка конкретного справочника
            if only == "categories":
                count = loader.load_categories()
                logger.info(f"Загружено {count} категорий")
            elif only == "brands":
                count = loader.load_brands()
                logger.info(f"Загружено {count} брендов")
            elif only == "countries":
                count = loader.load_countries()
                logger.info(f"Загружено {count} стран")
            elif only == "attributes":
                count = loader.load_attribute_models()
                logger.info(f"Загружено {count} атрибутивных моделей")
            else:
                logger.error(f"Неизвестный справочник: {only}")
                sys.exit(1)
        else:
            # Загрузка всех справочников
            stats = loader.load_all()
            logger.info(f"Статистика загрузки: {stats}")
    
    finally:
        session.close()


def cmd_sync_products_full() -> None:
    """Полная синхронизация всех товаров."""
    logger.info("Запуск полной синхронизации товаров...")
    
    client = NKCatalogClient()
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    
    manager = ProductSyncManager(client, SessionLocal)
    stats = manager.sync_full()
    
    logger.info(f"Результат: {stats}")


def cmd_sync_products_incremental() -> None:
    """Инкрементальная синхронизация товаров."""
    logger.info("Запуск инкрементальной синхронизации товаров...")
    
    client = NKCatalogClient()
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    
    manager = ProductSyncManager(client, SessionLocal)
    stats = manager.sync_incremental()
    
    logger.info(f"Результат: {stats}")


def cmd_sync_products_reset() -> None:
    """Сброс прогресса синхронизации."""
    logger.info("Сброс прогресса синхронизации...")
    
    client = NKCatalogClient()
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    
    manager = ProductSyncManager(client, SessionLocal)
    manager.reset_progress()


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Интеграция с Национальным Каталогом (Честный Знак)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")
    
    # Команда load-catalogs
    catalogs_parser = subparsers.add_parser(
        "load-catalogs",
        help="Загрузить справочники Национального Каталога"
    )
    catalogs_parser.add_argument(
        "--only",
        type=str,
        choices=["categories", "brands", "countries", "attributes"],
        help="Загрузить только указанный справочник",
    )
    
    # Команда sync-products-full
    full_parser = subparsers.add_parser(
        "sync-products-full",
        help="Полная синхронизация всех товаров"
    )
    full_parser.set_defaults(func=cmd_sync_products_full)
    
    # Команда sync-products-incremental
    inc_parser = subparsers.add_parser(
        "sync-products-incremental",
        help="Инкрементальная синхронизация товаров"
    )
    inc_parser.set_defaults(func=cmd_sync_products_incremental)
    
    # Команда sync-products-reset
    reset_parser = subparsers.add_parser(
        "sync-products-reset",
        help="Сброс прогресса синхронизации"
    )
    reset_parser.set_defaults(func=cmd_sync_products_reset)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    
    if args.command == "load-catalogs":
        cmd_load_catalogs(only=args.only)
    elif hasattr(args, 'func'):
        args.func()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
