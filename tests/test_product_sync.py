import pytest
import responses
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from product_sync import ProductSyncManager
from models import Base, Product, ProductAttribute, ProductPackage, ProductSetItem, ProductImage, SyncState
from nk_client import NKCatalogClient

# Конфигурация тестовой БД
engine = create_engine("sqlite:///:memory:", echo=False)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)


@pytest.fixture
def mock_client():
    client = MagicMock(spec=NKCatalogClient)
    client.api_usage_left = 500
    client.method_usage_left = 100
    return client


@pytest.fixture
def sync_manager(mock_client):
    return ProductSyncManager(mock_client, SessionLocal)


@pytest.fixture
def session():
    s = SessionLocal()
    yield s
    s.rollback()
    # Очистка таблиц между тестами
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    s.commit()


class TestProductSync:
    
    @responses.activate
    def test_sync_full_small_account(self, sync_manager, mock_client, session):
        """Тест полной синхронизации малого аккаунта (<10к товаров)."""
        # Мокаем product-list
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v4/product-list",
            json={
                "result": {
                    "total": 2,
                    "goods": [
                        {"good_id": 101, "gtin": "123", "good_name": "Test1"},
                        {"good_id": 102, "gtin": "456", "good_name": "Test2"}
                    ]
                }
            },
            status=200
        )
        
        # Мокаем feed-product
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v3/feed-product",
            json={
                "result": [
                    {
                        "good_id": 101,
                        "good_name": "Test1 Full",
                        "identified_by": [{"type": "gtin", "value": "123", "level": "trade-unit"}],
                        "good_attrs": [],
                        "good_images": [],
                        "set_gtins": []
                    },
                    {
                        "good_id": 102,
                        "good_name": "Test2 Full",
                        "identified_by": [{"type": "gtin", "value": "456", "level": "trade-unit"}],
                        "good_attrs": [],
                        "good_images": [],
                        "set_gtins": []
                    }
                ]
            },
            status=200
        )

        stats = sync_manager.sync_full()
        
        assert stats.total_remote == 2
        assert stats.new_products == 2
        assert stats.errors == 0
        
        # Проверка БД
        products = session.query(Product).all()
        assert len(products) == 2

    @responses.activate
    def test_sync_full_with_date_splitting(self, sync_manager, mock_client, session):
        """Тест нарезки периодов при HTTP 413."""
        # Первый вызов -> 413
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v4/product-list",
            json={"error": "Request Entity Too Large"},
            status=413
        )
        
        # Второй вызов (первая половина) -> OK
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v4/product-list",
            json={"result": {"total": 1, "goods": [{"good_id": 1, "gtin": "1"}]}},
            status=200
        )
        
        # Третий вызов (вторая половина) -> OK
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v4/product-list",
            json={"result": {"total": 1, "goods": [{"good_id": 2, "gtin": "2"}]}},
            status=200
        )
        
        # Feed-product моки (упрощённо)
        responses.add(responses.GET, "https://catalogapi.chestnyznak.ru/v3/feed-product", 
                      json={"result": []}, status=200)

        stats = sync_manager.sync_full()
        # Должен был разбить период и собрать товары
        assert stats.total_remote == 2

    @responses.activate
    def test_sync_incremental_detects_changes(self, sync_manager, mock_client, session):
        """Тест определения новых, изменённых и удалённых товаров."""
        # Создадим локальный товар с old_etag
        p_old = Product(good_id=100, gtin="100", etag="old_tag", is_deleted=False)
        p_removed = Product(good_id=200, gtin="200", etag="tag_rem", is_deleted=False)
        session.add_all([p_old, p_removed])
        session.commit()
        
        # Мокаем etagslist
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v3/etagslist",
            json={
                "result": {
                    "total": 2,
                    "goods": [
                        {"good_id": 100, "etag": "new_tag"}, # Изменился
                        {"good_id": 300, "etag": "tag_new"}  # Новый
                        # 200 нет -> удалён
                    ]
                }
            },
            status=200
        )
        
        # Мокаем feed-product для обновлённых
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v3/feed-product",
            json={
                "result": [
                    {"good_id": 100, "good_name": "Updated", "identified_by": [], "good_attrs": [], "good_images": [], "set_gtins": []},
                    {"good_id": 300, "good_name": "New", "identified_by": [], "good_attrs": [], "good_images": [], "set_gtins": []}
                ]
            },
            status=200
        )
        
        stats = sync_manager.sync_incremental()
        
        assert stats.new_products == 1 # 300
        assert stats.updated_products == 1 # 100
        assert stats.deleted_products == 1 # 200
        
        # Проверка удаления
        p_rem_db = session.query(Product).filter_by(good_id=200).first()
        assert p_rem_db.is_deleted == True

    @responses.activate
    def test_save_product_full_data(self, sync_manager, mock_client, session):
        """Тест сохранения всех связанных сущностей."""
        data = {
            "good_id": 999,
            "good_name": "Complex Product",
            "identified_by": [
                {"type": "gtin", "value": "111", "level": "trade-unit", "gtin": "111"},
                {"type": "gtin", "value": "222", "level": "box", "gtin": "222", "multiplier": 10}
            ],
            "good_attrs": [
                {"attr_id": 100, "attr_name": "Color", "attr_value": "Red"},
                {"attr_id": 2437, "attr_name": "Height", "attr_value": "10", "gtin": "222"} # Для упаковки
            ],
            "is_set": True,
            "set_gtins": [{"gtin": "333", "quantity": 2}],
            "good_images": [{"photo_url": "http://img.jpg", "photo_type": "main"}],
            "good_detailed_status": ["active"]
        }
        
        # Мокаем feed-product чтобы вернуть данные (хотя в тесте мы вызываем _save_product напрямую)
        # Но _process_batch вызывает enrich. Для чистоты вызовем _save_product напрямую.
        
        sync_manager._save_product(session, data, MagicMock())
        
        # Проверки
        prod = session.query(Product).filter_by(good_id=999).first()
        assert prod.good_name == "Complex Product"
        assert prod.gtin == "111" # Основной GTIN
        
        attrs = session.query(ProductAttribute).filter_by(good_id=999).all()
        assert len(attrs) == 2
        
        pkgs = session.query(ProductPackage).filter_by(good_id=999).all()
        assert len(pkgs) == 2
        box_pkg = [p for p in pkgs if p.level == 'box'][0]
        assert box_pkg.height == "10" # Заполнено из атрибута
        
        sets = session.query(ProductSetItem).filter_by(parent_good_id=999).all()
        assert len(sets) == 1
        assert sets[0].child_gtin == "333"
        
        imgs = session.query(ProductImage).filter_by(good_id=999).all()
        assert len(imgs) == 1

    @responses.activate
    def test_removed_products_marked_deleted(self, sync_manager, mock_client, session):
        """Удалённые товары помечаются флагом, а не удаляются."""
        p = Product(good_id=500, gtin="500")
        session.add(p)
        session.commit()
        
        # Etagslist не возвращает 500
        responses.add(
            responses.GET,
            "https://catalogapi.chestnyznak.ru/v3/etagslist",
            json={"result": {"total": 0, "goods": []}},
            status=200
        )
        
        stats = sync_manager.sync_incremental()
        
        assert stats.deleted_products == 1
        p_db = session.query(Product).filter_by(good_id=500).first()
        assert p_db.is_deleted == True

    def test_extract_main_gtin_trade_unit_priority(self, sync_manager):
        """Тест приоритета trade-unit GTIN."""
        identified_by = [
            {"type": "gtin", "value": "BOX_GTIN", "level": "box"},
            {"type": "gtin", "value": "MAIN_GTIN", "level": "trade-unit"},
            {"type": "gtin", "value": "PALLET_GTIN", "level": "pallet"},
        ]
        
        result = sync_manager._extract_main_gtin(identified_by)
        assert result == "MAIN_GTIN"

    def test_extract_main_gtin_fallback(self, sync_manager):
        """Тест fallback на первый GTIN."""
        identified_by = [
            {"type": "gtin", "value": "FIRST_GTIN", "level": "box"},
            {"type": "gtin", "value": "SECOND_GTIN", "level": "pallet"},
        ]
        
        result = sync_manager._extract_main_gtin(identified_by)
        assert result == "FIRST_GTIN"

    def test_extract_tnved_priority(self, sync_manager):
        """Тест приоритета кода ТНВЭД (13933 > 3959)."""
        good_attrs = [
            {"attr_id": 3959, "attr_value": "3959_CODE"},
            {"attr_id": 13933, "attr_value": "13933_CODE"},
        ]
        
        result = sync_manager._extract_tnved(good_attrs)
        assert result == "13933_CODE"

    def test_parse_dt_valid(self, sync_manager):
        """Тест парсинга валидной даты."""
        result = sync_manager._parse_dt("2024-01-15 10:30:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0)

    def test_parse_dt_invalid(self, sync_manager):
        """Тест парсинга невалидной даты."""
        result = sync_manager._parse_dt("invalid-date")
        assert result is None

    def test_parse_dt_none(self, sync_manager):
        """Тест парсинга None."""
        result = sync_manager._parse_dt(None)
        assert result is None

    def test_split_date_range(self, sync_manager):
        """Тест разделения диапазона дат."""
        mid = sync_manager._split_date_range("2020-01-01 00:00:00", "2020-01-02 00:00:00")
        # Середина должна быть 12 часов от начала
        assert "2020-01-01 12:00:00" == mid
