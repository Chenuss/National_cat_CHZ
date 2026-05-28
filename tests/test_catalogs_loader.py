"""
Тесты для CatalogsLoader.

Использует pytest + responses для мокирования HTTP-запросов к API.
"""

import pytest
import responses
import json
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Category, Brand, Country, AttributeModel, SyncState
from nk_client import NKCatalogClient, NKResponse
from catalogs_loader import CatalogsLoader
from sync_state_repo import SyncStateRepo


# ============================================================================
# ФИКСТУРЫ
# ============================================================================


@pytest.fixture
def engine():
    """Создаёт тестовый SQLite движок в памяти."""
    return create_engine("sqlite:///:memory:", echo=False)


@pytest.fixture
def session(engine):
    """Создаёт тестовую сессию SQLAlchemy."""
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@pytest.fixture
def mock_client():
    """Создаёт мок клиента API."""
    client = MagicMock(spec=NKCatalogClient)
    client._method_usage_left = 100
    client._api_usage_left = 500
    return client


@pytest.fixture
def loader(mock_client, session):
    """Создаёт CatalogsLoader с моком клиента и тестовой сессией."""
    return CatalogsLoader(mock_client, session)


# ============================================================================
# ТЕСТЫ: КАТЕГОРИИ
# ============================================================================


class TestLoadCategories:
    """Тесты загрузки категорий."""
    
    @responses.activate
    def test_load_categories_first_time(self, session):
        """Тест первой загрузки категорий (без сохранённого ETag)."""
        # Создаём клиент и лоадер
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Мокаем ответ API
        categories_data = [
            {"cat_id": 1, "cat_name": "Категория 1", "cat_parent_id": None, "cat_level": 1, "category_active": True},
            {"cat_id": 2, "cat_name": "Категория 2", "cat_parent_id": 1, "cat_level": 2, "category_active": True},
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/categories",
            json=categories_data,
            status=200,
            headers={"ETag": "abc123"},
        )
        
        # Запускаем загрузку
        count = loader.load_categories()
        
        # Проверяем результат
        assert count == 2
        
        # Проверяем данные в БД
        cats = session.query(Category).all()
        assert len(cats) == 2
        assert cats[0].cat_id == 1
        assert cats[1].cat_id == 2
        
        # Проверяем сохранение ETag
        sync_state = session.query(SyncState).filter_by(key="categories_etag").first()
        assert sync_state is not None
        assert sync_state.value["etag"] == "abc123"
    
    @responses.activate
    def test_load_categories_with_304(self, session):
        """Тест загрузки категорий с 304 на втором вызове."""
        # Создаём клиент и лоадер
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Сначала сохраняем ETag в БД
        repo = SyncStateRepo(session)
        repo.set_etag("categories_etag", "abc123")
        
        # Мокаем ответ 304
        responses.add(
            responses.GET,
            "https://test.api/v3/categories",
            status=304,
            headers={"ETag": "abc123"},
        )
        
        # Запускаем загрузку
        count = loader.load_categories()
        
        # Проверяем, что загрузка была пропущена
        assert count == 0
    
    @responses.activate
    def test_load_categories_gismt_codes(self, session):
        """Тест сохранения gismt_codes как массива целых чисел."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        categories_data = [
            {
                "cat_id": 1,
                "cat_name": "Категория 1",
                "cat_parent_id": None,
                "cat_level": 1,
                "category_active": True,
                "gismt_codes": [100, 200, 300],
            }
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/categories",
            json=categories_data,
            status=200,
            headers={"ETag": "xyz789"},
        )
        
        count = loader.load_categories()
        assert count == 1
        
        cat = session.query(Category).filter_by(cat_id=1).first()
        assert cat.gismt_codes == [100, 200, 300]


# ============================================================================
# ТЕСТЫ: БРЕНДЫ
# ============================================================================


class TestLoadBrands:
    """Тесты загрузки брендов."""
    
    @responses.activate
    def test_load_brands_pagination(self, session):
        """Тест загрузки брендов с пагинацией (2 страницы)."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Страница 1
        brands_page1 = [{"brand_id": i, "brand_name": f"Бренд {i}"} for i in range(1, 10001)]
        
        # Страница 2 (неполная)
        brands_page2 = [{"brand_id": i, "brand_name": f"Бренд {i}"} for i in range(10001, 10500)]
        
        # Мокаем первую страницу (с apikey параметром)
        responses.add(
            responses.GET,
            "https://test.api/v3/brands",
            json=brands_page1,
            status=200,
            headers={"ETag": "brands_etag_123"},
            match=[responses.matchers.query_param_matcher({"limit": "10000", "offset": "0", "apikey": "test_key"})],
        )
        
        # Мокаем вторую страницу
        responses.add(
            responses.GET,
            "https://test.api/v3/brands",
            json=brands_page2,
            status=200,
            match=[responses.matchers.query_param_matcher({"limit": "10000", "offset": "10000", "apikey": "test_key"})],
        )
        
        # Запускаем загрузку
        count = loader.load_brands()
        
        # Проверяем результат
        assert count == 10499  # 10000 + 499
        
        # Проверяем данные в БД
        total_brands = session.query(Brand).count()
        assert total_brands == 10499
        
        # Проверяем сохранение ETag
        sync_state = session.query(SyncState).filter_by(key="brands_etag").first()
        assert sync_state is not None
        assert sync_state.value["etag"] == "brands_etag_123"
    
    @responses.activate
    def test_load_brands_with_304(self, session):
        """Тест пропуска загрузки брендов при совпадении ETag."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Сохраняем ETag
        repo = SyncStateRepo(session)
        repo.set_etag("brands_etag", "existing_etag")
        
        # Мокаем ответ 304 (с apikey параметром)
        responses.add(
            responses.GET,
            "https://test.api/v3/brands",
            status=304,
            match=[responses.matchers.query_param_matcher({"limit": "10000", "offset": "0", "apikey": "test_key"})],
        )
        
        # Запускаем загрузку
        count = loader.load_brands()
        
        # Проверяем, что загрузка была пропущена
        assert count == 0
    
    @responses.activate
    def test_load_brands_party_brand_id(self, session):
        """Тест сохранения party_brand_id для субаккаунтов."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        brands_data = [
            {"brand_id": 1, "brand_name": "Бренд 1", "party_brand_id": 999},
            {"brand_id": 2, "brand_name": "Бренд 2"},  # без party_brand_id
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/brands",
            json=brands_data,
            status=200,
            headers={"ETag": "etag_xyz"},
            match=[responses.matchers.query_param_matcher({"limit": "10000", "offset": "0", "apikey": "test_key"})],
        )
        
        count = loader.load_brands()
        assert count == 2
        
        brand1 = session.query(Brand).filter_by(brand_id=1).first()
        assert brand1.party_brand_id == 999
        
        brand2 = session.query(Brand).filter_by(brand_id=2).first()
        assert brand2.party_brand_id is None


# ============================================================================
# ТЕСТЫ: СТРАНЫ
# ============================================================================


class TestLoadCountries:
    """Тесты загрузки стран."""
    
    @responses.activate
    def test_load_countries_cyrillic_keys(self, session):
        """Тест загрузки стран с кириллическими ключами."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Ответ API с кириллической "с" в ключах
        countries_response = {
            "apiversion": 3,
            "result": {
                "_etag": "countries_etag_456",
                "_list": [
                    {"сountry_iso": "RU", "сountry_name": "Россия"},
                    {"сountry_iso": "BY", "сountry_name": "Беларусь"},
                    {"сountry_iso": "KZ", "сountry_name": "Казахстан"},
                ]
            }
        }
        
        responses.add(
            responses.GET,
            "https://test.api/v3/dictionary/isocountry",
            json=countries_response,
            status=200,
        )
        
        # Запускаем загрузку
        count = loader.load_countries()
        
        # Проверяем результат
        assert count == 3
        
        # Проверяем данные в БД
        ru = session.query(Country).filter_by(code="RU").first()
        assert ru is not None
        assert ru.name_ru == "Россия"
        
        by = session.query(Country).filter_by(code="BY").first()
        assert by is not None
        assert by.name_ru == "Беларусь"
        
        # Проверяем сохранение ETag из JSON
        sync_state = session.query(SyncState).filter_by(key="countries_etag").first()
        assert sync_state is not None
        assert sync_state.value["etag"] == "countries_etag_456"
    
    @responses.activate
    def test_load_countries_with_saved_etag(self, session):
        """Тест пропуска загрузки стран при совпадении ETag."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Сохраняем ETag
        repo = SyncStateRepo(session)
        repo.set_etag("countries_etag", "same_etag")
        
        # Ответ API с тем же ETag
        countries_response = {
            "apiversion": 3,
            "result": {
                "_etag": "same_etag",
                "_list": []
            }
        }
        
        responses.add(
            responses.GET,
            "https://test.api/v3/dictionary/isocountry",
            json=countries_response,
            status=200,
        )
        
        # Запускаем загрузку
        count = loader.load_countries()
        
        # Проверяем, что загрузка была пропущена
        assert count == 0
    
    @responses.activate
    def test_load_countries_fallback_latin_keys(self, session):
        """Тест fallback на латинские ключи если кириллические отсутствуют."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Ответ с латинскими ключами (fallback)
        countries_response = {
            "apiversion": 3,
            "result": {
                "_etag": "etag_latin",
                "_list": [
                    {"country_iso": "US", "country_name": "США"},  # латинские ключи
                ]
            }
        }
        
        responses.add(
            responses.GET,
            "https://test.api/v3/dictionary/isocountry",
            json=countries_response,
            status=200,
        )
        
        count = loader.load_countries()
        assert count == 1
        
        us = session.query(Country).filter_by(code="US").first()
        assert us is not None
        assert us.name_ru == "США"


# ============================================================================
# ТЕСТЫ: АТРИБУТЫ
# ============================================================================


class TestLoadAttributeModels:
    """Тесты загрузки атрибутивных моделей."""
    
    @responses.activate
    def test_load_base_attributes(self, session):
        """Тест загрузки базового справочника атрибутов."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        attrs_data = [
            {"attr_id": 100, "attr_name": "Атрибут 1", "attr_field_type": "string"},
            {"attr_id": 101, "attr_name": "Атрибут 2", "attr_field_type": "number"},
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/attributes",
            json=attrs_data,
            status=200,
        )
        
        # Загружаем только базовые атрибуты
        count = loader._load_base_attributes()
        
        assert count == 2
        
        attr = session.query(AttributeModel).filter_by(attr_id=100).first()
        assert attr is not None
        assert attr.attr_name == "Атрибут 1"
        assert attr.cat_id is None  # Базовый атрибут без привязки к категории
    
    @responses.activate
    def test_load_attributes_skips_inactive_categories(self, session):
        """Тест пропуска неактивных категорий при загрузке атрибутов."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        # Создаём активную и неактивную категории
        active_cat = Category(cat_id=1, cat_name="Active", category_active=True)
        inactive_cat = Category(cat_id=2, cat_name="Inactive", category_active=False)
        session.add(active_cat)
        session.add(inactive_cat)
        session.commit()
        
        # Мокаем ответ для базовых атрибутов
        base_attrs = [{"attr_id": 100, "attr_name": "Base Attr", "attr_field_type": "string"}]
        responses.add(
            responses.GET,
            "https://test.api/v3/attributes",
            json=base_attrs,
            status=200,
            match=[responses.matchers.query_param_matcher({"apikey": "test_key"})],
        )
        
        # Мокаем ответ для активной категории
        attrs_data = [{"attr_id": 100, "attr_name": "Атрибут", "attr_type": "m"}]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/attributes",
            json=attrs_data,
            status=200,
            match=[responses.matchers.query_param_matcher({"cat_id": "1", "apikey": "test_key"})],
        )
        
        # Запускаем полную загрузку атрибутов
        count = loader.load_attribute_models()
        
        # Проверяем, что запрос был сделан только для активной категории
        # (для неактивной категории запрос не должен быть сделан)
        assert len(responses.calls) == 2  # 1 для base attributes + 1 для active category


# ============================================================================
# ТЕСТЫ: IDEMPOTENCY
# ============================================================================


class TestIdempotency:
    """Тесты идемпотентности загрузки."""
    
    @responses.activate
    def test_categories_upsert_no_duplicates(self, session):
        """Тест что повторная загрузка категорий не создаёт дублей."""
        client = NKCatalogClient(api_key="test_key", api_url="https://test.api")
        loader = CatalogsLoader(client, session)
        
        categories_data = [
            {"cat_id": 1, "cat_name": "Категория 1", "cat_parent_id": None, "cat_level": 1},
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/categories",
            json=categories_data,
            status=200,
            headers={"ETag": "etag_v1"},
        )
        
        # Первая загрузка
        count1 = loader.load_categories()
        assert count1 == 1
        
        # Вторая загрузка (обновление)
        categories_data_updated = [
            {"cat_id": 1, "cat_name": "Категория 1 Updated", "cat_parent_id": None, "cat_level": 1},
        ]
        
        responses.add(
            responses.GET,
            "https://test.api/v3/categories",
            json=categories_data_updated,
            status=200,
            headers={"ETag": "etag_v2"},
        )
        
        count2 = loader.load_categories()
        assert count2 == 1
        
        # Проверяем что запись одна и обновлена
        cats = session.query(Category).all()
        assert len(cats) == 1
        assert cats[0].cat_name == "Категория 1 Updated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
