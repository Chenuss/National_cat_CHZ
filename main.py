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
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    
    if args.command == "load-catalogs":
        cmd_load_catalogs(only=args.only)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
