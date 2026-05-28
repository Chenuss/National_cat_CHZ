"""
Загрузчик справочников Национального Каталога.

Фаза 2: Загрузка четырёх справочников:
1. Категории (/v3/categories)
2. Бренды (/v3/brands)
3. Страны (/v3/dictionary/isocountry)
4. Атрибутивные модели (/v3/attributes)

Поддерживает:
- ETag для инкрементального обновления
- Пагинацию для брендов
- Rate limiting awareness
- UPSERT стратегию (без удаления старых записей)
"""

import logging
import time
import json
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from nk_client import NKCatalogClient, NKResponse
from models import Category, Brand, Country, AttributeModel
from sync_state_repo import SyncStateRepo


logger = logging.getLogger(__name__)


class CatalogsLoader:
    """
    Загрузчик справочников Национального Каталога.
    
    Оркестрирует загрузку четырёх справочников с поддержкой ETag,
    пагинации и rate limiting.
    
    Пример использования:
        >>> client = NKCatalogClient()
        >>> session = Session()
        >>> loader = CatalogsLoader(client, session)
        >>> stats = loader.load_all()
        >>> print(f"Загружено: {stats}")
    """
    
    # Максимальный размер страницы для брендов
    BRANDS_PAGE_SIZE = 10000
    
    # Размер батча для UPSERT операций
    UPSERT_BATCH_SIZE = 500
    
    # Пауза между запросами атрибутов (секунды)
    ATTRIBUTES_DELAY = 0.5
    
    def __init__(self, client: NKCatalogClient, session: Session):
        """
        Инициализация загрузчика.
        
        Args:
            client: Клиент API Национального Каталога
            session: SQLAlchemy сессия
        """
        self.client = client
        self.session = session
        self.sync_state = SyncStateRepo(session)
        self.logger = logging.getLogger(__name__)
    
    def load_all(self) -> Dict[str, int]:
        """
        Загружает все справочники по порядку.
        
        Порядок загрузки:
        1. Категории (необходимы для атрибутов)
        2. Бренды
        3. Страны
        4. Атрибутивные модели (зависят от категорий)
        
        Returns:
            Словарь со статистикой: {"categories": N, "brands": N, "countries": N, "attributes": N}
        """
        self.logger.info("=" * 60)
        self.logger.info("Начало загрузки справочников Национального Каталога")
        self.logger.info("=" * 60)
        
        stats = {}
        
        # 1. Категории
        self.logger.info("\n[1/4] Загрузка категорий...")
        stats["categories"] = self.load_categories()
        
        # 2. Бренды
        self.logger.info("\n[2/4] Загрузка брендов...")
        stats["brands"] = self.load_brands()
        
        # 3. Страны
        self.logger.info("\n[3/4] Загрузка стран...")
        stats["countries"] = self.load_countries()
        
        # 4. Атрибутивные модели
        self.logger.info("\n[4/4] Загрузка атрибутивных моделей...")
        stats["attributes"] = self.load_attribute_models()
        
        self.logger.info("\n" + "=" * 60)
        self.logger.info("Загрузка справочников завершена")
        self.logger.info(f"Итого: категории={stats['categories']}, бренды={stats['brands']}, "
                        f"страны={stats['countries']}, атрибуты={stats['attributes']}")
        self.logger.info("=" * 60)
        
        return stats
    
    def load_categories(self) -> int:
        """
        Загружает справочник категорий из /v3/categories.
        
        API особенности:
        - Поддерживает HTTP ETag
        - Возвращает плоский массив всех категорий
        - Дерево строится по cat_parent_id
        
        Логика:
        1. Получить сохранённый ETag из sync_state
        2. Сделать GET /v3/categories с If-None-Match
        3. Если 304 — завершить с логом "Категории не изменились"
        4. Если данные изменились — сделать UPSERT в таблицу categories
        5. Сохранить новый ETag в sync_state
        
        Returns:
            Количество вставленных/обновлённых записей
        """
        # Получаем сохранённый ETag
        saved_etag = self.sync_state.get_etag("categories_etag")
        
        # Делаем запрос к API
        self.logger.info("Запрос категорий из API...")
        response = self.client.request(
            method="GET",
            endpoint="/v3/categories",
            etag=saved_etag,
            use_etag=True,
        )
        
        # Проверяем 304 Not Modified
        if response.status_code == 304 or not response.modified:
            self.logger.info("Категории не изменились (ETag совпал), пропускаем загрузку")
            return 0
        
        # Извлекаем данные
        if not response.has_data:
            self.logger.warning("Получен пустой ответ от API")
            return 0
        
        categories_data = response.data
        if not isinstance(categories_data, list):
            self.logger.warning(f"Ожидался массив категорий, получено: {type(categories_data)}")
            return 0
        
        self.logger.info(f"Получено {len(categories_data)} категорий из API")
        
        # UPSERT в базу данных
        count = self._upsert_categories(categories_data)
        
        # Сохраняем новый ETag
        if response.etag:
            self.sync_state.set_etag("categories_etag", response.etag)
            self.logger.info(f"Сохранён ETag категорий: {response.etag}")
        
        self.logger.info(f"Загружено {count} категорий")
        return count
    
    def _upsert_categories(self, categories_data: List[Dict[str, Any]]) -> int:
        """
        Выполняет UPSERT категорий в базу данных.
        
        Args:
            categories_data: Список категорий из API
            
        Returns:
            Количество вставленных/обновлённых записей
        """
        count = 0
        
        for cat in categories_data:
            try:
                cat_id = cat.get("cat_id")
                if cat_id is None:
                    continue
                
                # Извлекаем поля
                cat_name = cat.get("cat_name", "")
                cat_parent_id = cat.get("cat_parent_id")
                cat_level = cat.get("cat_level", 1)
                category_active = cat.get("category_active", True)
                if category_active is None:
                    category_active = True
                
                # gismt_codes — массив целых чисел, сохраняем как JSON
                gismt_codes_raw = cat.get("gismt_codes", [])
                if isinstance(gismt_codes_raw, list):
                    # Преобразуем в список int
                    gismt_codes = [int(x) for x in gismt_codes_raw if x is not None]
                else:
                    gismt_codes = []
                
                # tnved_codes — массив строк
                tnved_codes_raw = cat.get("tnved_codes", [])
                if isinstance(tnved_codes_raw, list):
                    tnved_codes = [str(x) for x in tnved_codes_raw if x is not None]
                else:
                    tnved_codes = []
                
                # UPSERT через merge
                category = self.session.query(Category).filter_by(cat_id=cat_id).first()
                
                if category is None:
                    category = Category(cat_id=cat_id)
                    self.session.add(category)
                
                category.cat_name = cat_name
                category.cat_parent_id = cat_parent_id
                category.cat_level = cat_level
                category.category_active = category_active
                category.gismt_codes = gismt_codes
                category.tnved_codes = tnved_codes
                
                count += 1
                
                # Коммитим батчами
                if count % self.UPSERT_BATCH_SIZE == 0:
                    self.session.commit()
                    self.logger.debug(f"Закоммичено {count} категорий")
                
            except Exception as e:
                self.logger.warning(f"Ошибка при обработке категории {cat.get('cat_id')}: {e}")
                continue
        
        # Финальный коммит
        self.session.commit()
        return count
    
    def load_brands(self) -> int:
        """
        Загружает справочник брендов из /v3/brands.
        
        API особенности:
        - Поддерживает HTTP ETag
        - Пагинация: limit (макс. 10000), offset
        - Ответ: массив объектов {brand_id, brand_name}
        - Иногда встречается поле party_brand_id (для субаккаунтов)
        
        Логика:
        1. Получить ETag из sync_state
        2. Сделать первый запрос с If-None-Match
        3. Если 304 — пропустить всю загрузку
        4. Иначе — цикл с пагинацией до исчерпания данных
        5. После полного цикла — сохранить новый ETag
        
        Returns:
            Количество вставленных/обновлённых записей
        """
        # Получаем сохранённый ETag
        saved_etag = self.sync_state.get_etag("brands_etag")
        
        # Первый запрос для проверки ETag
        self.logger.info("Первый запрос брендов для проверки ETag...")
        response = self.client.request(
            method="GET",
            endpoint="/v3/brands",
            params={"limit": self.BRANDS_PAGE_SIZE, "offset": 0},
            etag=saved_etag,
            use_etag=True,
        )
        
        # Проверяем 304 Not Modified
        if response.status_code == 304 or not response.modified:
            self.logger.info("Бренды не изменились (ETag совпал), пропускаем загрузку")
            return 0
        
        # Начинаем загрузку с пагинацией
        total_count = 0
        offset = 0
        new_etag = response.etag
        
        while True:
            # Если это не первый запрос, делаем запрос без ETag
            if offset > 0:
                response = self.client.request(
                    method="GET",
                    endpoint="/v3/brands",
                    params={"limit": self.BRANDS_PAGE_SIZE, "offset": offset},
                    use_etag=False,
                )
            
            # Извлекаем данные
            if not response.has_data:
                self.logger.debug(f"Пустой ответ на offset={offset}, завершаем пагинацию")
                break
            
            brands_data = response.data
            if not isinstance(brands_data, list):
                self.logger.warning(f"Ожидался массив брендов, получено: {type(brands_data)}")
                break
            
            if len(brands_data) == 0:
                break
            
            self.logger.info(f"Загружено {len(brands_data)} брендов (offset={offset})")
            
            # UPSERT брендов
            count = self._upsert_brands(brands_data)
            total_count += count
            
            # Проверяем, есть ли ещё данные
            if len(brands_data) < self.BRANDS_PAGE_SIZE:
                self.logger.debug(f"Последняя страница ({len(brands_data)} < {self.BRANDS_PAGE_SIZE})")
                break
            
            offset += self.BRANDS_PAGE_SIZE
            
            # Rate limiting check
            if self.client._method_usage_left is not None and self.client._method_usage_left <= 5:
                self.logger.info("Мало запросов метода осталось, пауза 1 сек...")
                time.sleep(1.0)
        
        # Сохраняем ETag только после полной загрузки
        if new_etag:
            state_value = {
                "etag": new_etag,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total_count": total_count,
            }
            self.sync_state.set_state("brands_etag", state_value)
            self.logger.info(f"Сохранён ETag брендов: {new_etag} (всего {total_count})")
        
        self.logger.info(f"Загружено {total_count} брендов")
        return total_count
    
    def _upsert_brands(self, brands_data: List[Dict[str, Any]]) -> int:
        """
        Выполняет UPSERT брендов в базу данных.
        
        Args:
            brands_data: Список брендов из API
            
        Returns:
            Количество вставленных/обновлённых записей
        """
        count = 0
        
        for brand in brands_data:
            try:
                brand_id = brand.get("brand_id")
                if brand_id is None:
                    continue
                
                brand_name = brand.get("brand_name", "")
                party_brand_id = brand.get("party_brand_id")
                
                # UPSERT через merge
                db_brand = self.session.query(Brand).filter_by(brand_id=brand_id).first()
                
                if db_brand is None:
                    db_brand = Brand(brand_id=brand_id)
                    self.session.add(db_brand)
                
                db_brand.brand_name = brand_name
                db_brand.party_brand_id = party_brand_id
                
                count += 1
                
            except Exception as e:
                self.logger.warning(f"Ошибка при обработке бренда {brand.get('brand_id')}: {e}")
                continue
        
        self.session.commit()
        return count
    
    def load_countries(self) -> int:
        """
        Загружает справочник стран из /v3/dictionary/isocountry.
        
        ⚠️ КРИТИЧЕСКАЯ ЛОВУШКА:
        В ответе поля называются с кириллической "с" (U+0441), а не латинской "c":
        - сountry_iso (НЕ country_iso)
        - сountry_name (НЕ country_name)
        
        Также ETag возвращается ВНУТРИ JSON (в поле _etag), а не в HTTP-заголовке!
        
        Структура ответа:
        {
          "apiversion": 3,
          "result": {
            "_etag": "0a23f98d522e7c05",
            "_list": [
              {"сountry_iso": "RU", "сountry_name": "Россия"},
              ...
            ]
          }
        }
        
        Returns:
            Количество вставленных/обновлённых записей
        """
        # Получаем сохранённый ETag
        saved_etag = self.sync_state.get_etag("countries_etag")
        
        # Делаем запрос к API
        self.logger.info("Запрос стран из API...")
        response = self.client.request(
            method="GET",
            endpoint="/v3/dictionary/isocountry",
            use_etag=False,  # ETag внутри JSON, не в заголовке
        )
        
        if not response.has_data:
            self.logger.warning("Получен пустой ответ от API")
            return 0
        
        # Извлекаем ETag из JSON (критически важно!)
        result = response.data.get("result", {})
        current_etag = result.get("_etag")
        
        # Проверяем, изменились ли данные
        if current_etag and saved_etag and current_etag == saved_etag:
            self.logger.info("Страны не изменились (ETag совпал), пропускаем загрузку")
            return 0
        
        # Извлекаем список стран
        countries_list = result.get("_list", [])
        
        if not countries_list:
            self.logger.warning("Список стран пуст в ответе API")
            return 0
        
        self.logger.info(f"Получено {len(countries_list)} стран из API")
        
        # UPSERT в базу данных
        count = self._upsert_countries(countries_list)
        
        # Сохраняем новый ETag из JSON
        if current_etag:
            self.sync_state.set_etag("countries_etag", current_etag)
            self.logger.info(f"Сохранён ETag стран: {current_etag}")
        
        self.logger.info(f"Загружено {count} стран")
        return count
    
    def _upsert_countries(self, countries_list: List[Dict[str, Any]]) -> int:
        """
        Выполняет UPSERT стран в базу данных.
        
        ⚠️ Обработка кириллической "с" в названиях полей.
        
        Args:
            countries_list: Список стран из API
            
        Returns:
            Количество вставленных/обновлённых записей
        """
        count = 0
        
        for country in countries_list:
            try:
                # КРИТИЧЕСКИ ВАЖНО: пробуем кириллическую "с" сначала, затем латинскую
                # Кириллическая "с" = U+0441, латинская "c" = U+0063
                country_code = country.get("сountry_iso") or country.get("country_iso")
                country_name = country.get("сountry_name") or country.get("country_name")
                
                if not country_code:
                    self.logger.warning(f"Пропущена страна без кода: {country}")
                    continue
                
                # UPSERT через merge
                db_country = self.session.query(Country).filter_by(code=country_code).first()
                
                if db_country is None:
                    db_country = Country(code=country_code)
                    self.session.add(db_country)
                
                db_country.name_ru = country_name or ""
                # name_en оставляем None или можно попробовать взять из другого поля если есть
                
                count += 1
                
            except Exception as e:
                self.logger.warning(f"Ошибка при обработке страны {country}: {e}")
                continue
        
        self.session.commit()
        return count
    
    def load_attribute_models(self) -> int:
        """
        Загружает атрибутивные модели из /v3/attributes.
        
        Двухуровневая стратегия:
        
        Шаг А. Базовый справочник атрибутов (без привязки к категории):
        - Запрос GET /v3/attributes без параметров
        - Сохранение с cat_id = NULL
        
        Шаг Б. Обогащение по категориям:
        - Для каждой активной категории из таблицы categories
        - Запрос GET /v3/attributes?cat_id={cat_id}
        - Обновление записей с проставлением cat_id, is_required, и др.
        
        Returns:
            Количество вставленных/обновлённых записей
        """
        total_count = 0
        
        # ШАГ А: Базовый справочник атрибутов
        self.logger.info("Шаг А: Загрузка базового справочника атрибутов...")
        base_count = self._load_base_attributes()
        total_count += base_count
        self.logger.info(f"Загружено {base_count} базовых атрибутов")
        
        # ШАГ Б: Обогащение по категориям
        self.logger.info("Шаг Б: Обогащение атрибутов по категориям...")
        
        # Получаем активные категории
        active_categories = (
            self.session.query(Category)
            .filter_by(category_active=True)
            .all()
        )
        
        self.logger.info(f"Найдено {len(active_categories)} активных категорий")
        
        # Обрабатываем категории батчами по 10 для rate limiting
        batch_size = 10
        cats_processed = 0
        
        for i in range(0, len(active_categories), batch_size):
            batch = active_categories[i:i + batch_size]
            
            for category in batch:
                try:
                    cat_id = category.cat_id
                    
                    # Запрос атрибутов для категории
                    response = self.client.request(
                        method="GET",
                        endpoint="/v3/attributes",
                        params={"cat_id": cat_id},
                        use_etag=False,
                    )
                    
                    if not response.has_data:
                        continue
                    
                    attrs_data = response.data
                    if not isinstance(attrs_data, list):
                        continue
                    
                    # Обновляем атрибуты с привязкой к категории
                    count = self._update_attributes_for_category(cat_id, attrs_data)
                    cats_processed += 1
                    
                    if cats_processed % 10 == 0:
                        self.logger.info(f"Обработано {cats_processed} из {len(active_categories)} категорий")
                    
                except Exception as e:
                    self.logger.warning(f"Ошибка при обработке категории {category.cat_id}: {e}")
                    continue
            
            # Пауза между батчами для rate limiting
            if i + batch_size < len(active_categories):
                time.sleep(self.ATTRIBUTES_DELAY)
        
        self.logger.info(f"Обработано {cats_processed} категорий")
        self.logger.info(f"Всего загружено/обновлено {total_count} атрибутивных моделей")
        
        return total_count
    
    def _load_base_attributes(self) -> int:
        """
        Загружает базовый справочник атрибутов (без привязки к категории).
        
        Returns:
            Количество вставленных/обновлённых записей
        """
        response = self.client.request(
            method="GET",
            endpoint="/v3/attributes",
            use_etag=False,
        )
        
        if not response.has_data:
            return 0
        
        attrs_data = response.data
        if not isinstance(attrs_data, list):
            return 0
        
        count = 0
        
        for attr in attrs_data:
            try:
                attr_id = attr.get("attr_id")
                if attr_id is None:
                    continue
                
                attr_name = attr.get("attr_name", "")
                attr_field_type = attr.get("attr_field_type")
                requirement = attr.get("requirement")
                is_multiplicable = attr.get("is_multiplicable", False)
                layer = attr.get("layer")
                preset_url = attr.get("preset_url")
                
                # UPSERT с cat_id=NULL
                db_attr = self.session.query(AttributeModel).filter_by(
                    attr_id=attr_id, cat_id=None
                ).first()
                
                if db_attr is None:
                    # Проверяем, нет ли уже записи с таким attr_id для какой-то категории
                    existing = self.session.query(AttributeModel).filter_by(
                        attr_id=attr_id
                    ).first()
                    if existing:
                        # Обновляем существующую запись если она без cat_id
                        db_attr = existing
                    else:
                        db_attr = AttributeModel(attr_id=attr_id)
                        self.session.add(db_attr)
                
                db_attr.attr_name = attr_name
                db_attr.attr_field_type = attr_field_type
                db_attr.requirement = requirement
                db_attr.is_multiplicable = is_multiplicable if is_multiplicable is not None else False
                db_attr.layer = layer
                db_attr.preset_url = preset_url
                
                count += 1
                
            except Exception as e:
                self.logger.warning(f"Ошибка при обработке атрибута {attr.get('attr_id')}: {e}")
                continue
        
        self.session.commit()
        return count
    
    def _update_attributes_for_category(self, cat_id: int, attrs_data: List[Dict[str, Any]]) -> int:
        """
        Обновляет атрибуты для конкретной категории.
        
        Args:
            cat_id: ID категории
            attrs_data: Список атрибутов из API
            
        Returns:
            Количество обновлённых записей
        """
        count = 0
        
        for attr in attrs_data:
            try:
                attr_id = attr.get("attr_id")
                if attr_id is None:
                    continue
                
                attr_name = attr.get("attr_name", "")
                attr_field_type = attr.get("attr_field_type")
                requirement = attr.get("requirement")
                is_multiplicable = attr.get("is_multiplicable", False)
                layer = attr.get("layer")
                preset_url = attr.get("preset_url")
                
                # Определяем is_required по attr_type ('m' = mandatory)
                attr_type = attr.get("attr_type", "")
                is_required = (attr_type == "m")
                
                # Сохраняем attr_preset и dependent_attributes как JSON
                attr_preset = attr.get("attr_preset", [])
                dependent_attributes = attr.get("dependent_attributes")
                
                # UPSERT с cat_id
                db_attr = self.session.query(AttributeModel).filter_by(
                    cat_id=cat_id, attr_id=attr_id
                ).first()
                
                if db_attr is None:
                    db_attr = AttributeModel(cat_id=cat_id, attr_id=attr_id)
                    self.session.add(db_attr)
                
                db_attr.attr_name = attr_name
                db_attr.attr_field_type = attr_field_type
                db_attr.requirement = requirement
                db_attr.is_multiplicable = is_multiplicable if is_multiplicable is not None else False
                db_attr.layer = layer
                db_attr.preset_url = preset_url
                db_attr.is_required = is_required
                
                # Сохраняем сложные поля как JSON (если модель поддерживает)
                # Примечание: в текущей модели AttributeModel нет полей для attr_preset
                # и dependent_attributes, но мы можем расширить модель при необходимости
                
                count += 1
                
            except Exception as e:
                self.logger.warning(f"Ошибка при обработке атрибута {attr.get('attr_id')} для cat_id={cat_id}: {e}")
                continue
        
        self.session.commit()
        return count
