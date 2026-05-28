import logging
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import func, delete

from nk_client import NKCatalogClient, NKApiError, NKRequestTooLargeError, NKResponse
from models import (
    Product, ProductAttribute, ProductPackage, ProductSetItem, ProductImage,
    SyncState
)
from sync_state_repo import SyncStateRepo

# Импортируем SyncStats из отдельного модуля
try:
    from models_sync_stats import SyncStats
except ImportError:
    # Fallback для случаев когда файл называется иначе
    from dataclasses import dataclass
    
    @dataclass
    class SyncStats:
        """Статистика выполнения синхронизации товаров."""
        total_remote: int = 0
        local_existing: int = 0
        new_products: int = 0
        updated_products: int = 0
        unchanged_products: int = 0
        deleted_products: int = 0
        errors: int = 0
        duration_seconds: float = 0.0
        
        def __str__(self) -> str:
            return (
                f"SyncStats(total={self.total_remote}, new={self.new_products}, "
                f"updated={self.updated_products}, unchanged={self.unchanged_products}, "
                f"deleted={self.deleted_products}, errors={self.errors}, "
                f"duration={self.duration_seconds:.2f}s)"
            )

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
MAX_PRODUCT_LIST_LIMIT = 10000
PRODUCT_LIST_PAGE_SIZE = 1000


class ProductSyncManager:
    """
    Менеджер синхронизации товаров Национального Каталога.
    
    Поддерживает полную синхронизацию (product-list + feed-product)
    и инкрементальную (etagslist + delta feed-product).
    """

    def __init__(self, client: NKCatalogClient, session_factory: sessionmaker):
        self.client = client
        self.session_factory = session_factory
        self.state_repo = None  # Будет создан внутри методов синхронизации

    def sync_full(self) -> SyncStats:
        """
        Полная синхронизация всех товаров аккаунта.
        1. Получает список всех good_id через product-list (с нарезкой по датам при необходимости).
        2. Загружает полные данные батчами через feed-product.
        3. Сохраняет в БД.
        """
        logger.info("=" * 60)
        logger.info("Запуск ПОЛНОЙ синхронизации товаров")
        logger.info("=" * 60)
        
        start_time = time.time()
        stats = SyncStats()

        with self.session_factory() as session:
            # Инициализируем репозиторий состояния
            self.state_repo = SyncStateRepo(session)
            
            try:
                # Шаг 1: Получение списка ID
                logger.info("Этап 1: Получение списка товаров (product-list)...")
                goods_list = self._fetch_all_good_ids(session)
                stats.total_remote = len(goods_list)
                logger.info(f"Найдено {stats.total_remote} товаров на сервере.")

                if stats.total_remote == 0:
                    logger.info("Товаров для синхронизации нет.")
                    return stats

                # Шаг 2: Обогащение батчами
                logger.info("Этап 2: Загрузка полных карточек (feed-product)...")
                good_ids = [g['good_id'] for g in goods_list]
                
                for i in range(0, len(good_ids), BATCH_SIZE):
                    batch = good_ids[i:i + BATCH_SIZE]
                    self._process_batch(session, batch, stats, is_incremental=False)
                    
                    # Rate limit check
                    self._handle_rate_limit()

                # Коммит прогресса
                self.state_repo.set_state("last_full_sync", {
                    "timestamp": datetime.now().isoformat(),
                    "total": stats.total_remote
                })
                session.commit()

            except Exception as e:
                logger.error(f"Критическая ошибка при полной синхронизации: {e}", exc_info=True)
                session.rollback()
                raise
            finally:
                stats.duration_seconds = time.time() - start_time

        logger.info(f"Полная синхронизация завершена: {stats}")
        return stats

    def sync_incremental(self) -> SyncStats:
        """
        Инкрементальная синхронизация.
        1. Получает актуальные ETag всех товаров через etagslist.
        2. Сравнивает с локальными ETag.
        3. Загружает только новые и изменённые товары.
        4. Помечает удалённые товары.
        """
        logger.info("=" * 60)
        logger.info("Запуск ИНКРЕМЕНТАЛЬНОЙ синхронизации товаров")
        logger.info("=" * 60)

        start_time = time.time()
        stats = SyncStats()

        with self.session_factory() as session:
            # Инициализируем репозиторий состояния
            self.state_repo = SyncStateRepo(session)
            
            try:
                # Шаг 1: Получение remote ETags
                logger.info("Этап 1: Получение списка ETag (etagslist)...")
                remote_etags = self._fetch_etags_list()
                stats.total_remote = len(remote_etags)
                logger.info(f"Получено ETag для {stats.total_remote} товаров.")

                # Шаг 2: Сравнение с локальными
                logger.info("Этап 2: Анализ изменений...")
                local_etags = self._get_local_etags(session)
                
                remote_keys = set(remote_etags.keys())
                local_keys = set(local_etags.keys())

                new_ids = list(remote_keys - local_keys)
                removed_ids = list(local_keys - remote_keys)
                
                changed_ids = []
                unchanged_count = 0
                
                # Проверка изменённых
                common_ids = remote_keys & local_keys
                for gid in common_ids:
                    if remote_etags[gid] != local_etags[gid]:
                        changed_ids.append(gid)
                    else:
                        unchanged_count += 1
                
                stats.unchanged_products = unchanged_count
                logger.info(f"Новых: {len(new_ids)}, Изменённых: {len(changed_ids)}, "
                            f"Удалённых: {len(removed_ids)}, Без изменений: {unchanged_count}")

                # Шаг 3: Обработка удалённых
                if removed_ids:
                    logger.info(f"Помечаем {len(removed_ids)} удалённых товаров...")
                    (session.query(Product)
                     .filter(Product.good_id.in_(removed_ids))
                     .update({Product.is_deleted: True, Product.update_date: datetime.now()},
                             synchronize_session='fetch'))
                    stats.deleted_products = len(removed_ids)
                    session.commit()

                # Шаг 4: Загрузка обновлений
                to_update = new_ids + changed_ids
                if to_update:
                    logger.info(f"Загрузка {len(to_update)} обновлённых товаров...")
                    for i in range(0, len(to_update), BATCH_SIZE):
                        batch = to_update[i:i + BATCH_SIZE]
                        self._process_batch(session, batch, stats, is_incremental=True)
                        self._handle_rate_limit()
                
                # Сохранение снапшота ETag
                self.state_repo.set_state("remote_etags_snapshot", {
                    "timestamp": datetime.now().isoformat(),
                    "count": len(remote_etags)
                })
                session.commit()

            except Exception as e:
                logger.error(f"Критическая ошибка при инкрементальной синхронизации: {e}", exc_info=True)
                session.rollback()
                raise
            finally:
                stats.duration_seconds = time.time() - start_time

        logger.info(f"Инкрементальная синхронизация завершена: {stats}")
        return stats

    def _fetch_all_good_ids(self, session: Session) -> List[Dict[str, Any]]:
        """
        Получает все good_id через /v4/product-list.
        Реализует умную нарезку по датам при превышении лимита 10к.
        """
        all_goods = []
        
        # Начальный период: с начала работы системы до сейчас
        start_date = "2020-01-01 00:00:00"
        end_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        queue = [(start_date, end_date)]
        
        while queue:
            d_from, d_to = queue.pop(0)
            logger.debug(f"Запрос product-list: [{d_from}] - [{d_to}]")
            
            try:
                offset = 0
                page_goods = []
                total_count = None
                
                while True:
                    response = self.client.get_product_list(
                        from_date=d_from,
                        to_date=d_to,
                        limit=PRODUCT_LIST_PAGE_SIZE,
                        offset=offset
                    )
                    
                    resp_data = response.data
                    
                    if not resp_data:
                        break
                    
                    # Структура ответа v4: { result: { goods: [...], total: N } }
                    result = resp_data.get('result', {})
                    goods = result.get('goods', [])
                    total_count = result.get('total', 0)
                    
                    if not goods:
                        break
                        
                    page_goods.extend(goods)
                    
                    # Если получили меньше чем лимит — страница последняя
                    if len(goods) < PRODUCT_LIST_PAGE_SIZE:
                        break
                        
                    offset += PRODUCT_LIST_PAGE_SIZE
                    
                    # Защита от бесконечного цикла если total не совпадает
                    if offset >= MAX_PRODUCT_LIST_LIMIT:
                        logger.warning(f"Достигнут лимит оффсета {MAX_PRODUCT_LIST_LIMIT} для периода. Прерываем.")
                        break

                # Анализ результата
                if total_count and total_count > MAX_PRODUCT_LIST_LIMIT:
                    # Лимит превышен, нужно дробить период
                    logger.warning(f"Период [{d_from} - {d_to}] содержит > {MAX_PRODUCT_LIST_LIMIT} записей ({total_count}). Дробим.")
                    mid_date = self._split_date_range(d_from, d_to)
                    queue.append((mid_date, d_to))
                    queue.append((d_from, mid_date))
                    continue
                
                # Успешно выгрузили период
                all_goods.extend(page_goods)
                logger.info(f"Загружено {len(page_goods)} товаров за период [{d_from} - {d_to}]")
                
                # Сохраняем прогресс
                self.state_repo.set_state("product_list_progress", {
                    "last_period": [d_from, d_to],
                    "count": len(all_goods)
                })

            except NKRequestTooLargeError as e:
                if e.status_code == 413:
                    logger.warning(f"HTTP 413 для периода [{d_from} - {d_to}]. Дробим период.")
                    mid_date = self._split_date_range(d_from, d_to)
                    queue.append((mid_date, d_to))
                    queue.append((d_from, mid_date))
                else:
                    raise
            except Exception as e:
                logger.error(f"Неожиданная ошибка при загрузке product-list: {e}", exc_info=True)
                raise

        return all_goods

    def _split_date_range(self, d_from: str, d_to: str) -> str:
        """Разделяет диапазон дат пополам."""
        fmt = "%Y-%m-%d %H:%M:%S"
        dt_from = datetime.strptime(d_from, fmt)
        dt_to = datetime.strptime(d_to, fmt)
        delta = (dt_to - dt_from) / 2
        mid = dt_from + delta
        return mid.strftime(fmt)

    def _fetch_etags_list(self) -> Dict[int, str]:
        """Получает {good_id: etag} через /v3/etagslist с пагинацией."""
        all_etags = {}
        offset = 0
        
        while True:
            response = self.client.get_etags_list()
            resp_data = response.data
            
            if not resp_data:
                break
            
            result = resp_data.get('result', {})
            goods = result.get('goods', [])
            total = result.get('total', 0)
            
            for item in goods:
                all_etags[item['good_id']] = item['etag']
            
            logger.debug(f"Etagslist: загружено {len(all_etags)} из {total} (offset={offset})")
            
            if not goods or len(goods) < 100 or offset + 100 >= total:
                break
                
            offset += 100
            
            # Rate limit внутри цикла
            if self.client.method_usage_left < 5:
                time.sleep(1.0)
                
        return all_etags

    def _get_local_etags(self, session: Session) -> Dict[int, str]:
        """Загружает текущие ETag из БД."""
        rows = session.query(Product.good_id, Product.etag).filter(
            Product.etag.isnot(None),
            Product.is_deleted == False
        ).all()
        return {row.good_id: row.etag for row in rows if row.etag}

    def _enrich_products_batch(self, good_ids: List[int]) -> List[Dict[str, Any]]:
        """Загружает полные карточки батчем через /v3/feed-product."""
        ids_str = ";".join(map(str, good_ids))
        params = {"good_ids": ids_str, "subaccount": "true"}
        
        try:
            response = self.client.request("GET", "/v3/feed-product", params=params)
            resp_data = response.data
            new_etag = response.etag
            
            if not resp_data:
                return []
            
            # Парсинг ответа
            result = resp_data.get('result')
            
            products = []
            if isinstance(result, list):
                products = result
            elif isinstance(result, dict) and 'goods' in result:
                products = result['goods']
            elif isinstance(result, dict):
                products = [result]
            
            # Привязываем ETag ответа к товарам (он общий для батча)
            for p in products:
                p['_batch_etag'] = new_etag
                
            return products
            
        except NKApiError as e:
            if e.status_code == 304:
                logger.debug(f"Батч {good_ids} не изменился (304).")
                return []
            raise

    def _process_batch(self, session: Session, good_ids: List[int], stats: SyncStats, is_incremental: bool):
        """Обрабатывает батч ID: загружает данные и сохраняет в БД."""
        try:
            products_data = self._enrich_products_batch(good_ids)
        except NKApiError as e:
            if e.status_code == 304 and is_incremental:
                stats.unchanged_products += len(good_ids)
                return
            else:
                logger.error(f"Ошибка загрузки батча {good_ids}: {e}")
                stats.errors += len(good_ids)
                return

        for p_data in products_data:
            try:
                self._save_product(session, p_data, stats)
            except Exception as e:
                logger.error(f"Ошибка сохранения товара {p_data.get('good_id')}: {e}", exc_info=True)
                stats.errors += 1
                session.rollback()
                pass
        
        session.flush()

    def _save_product(self, session: Session, data: Dict[str, Any], stats: SyncStats):
        """Сохраняет один товар и связанные сущности (атомарно для товара)."""
        good_id = data.get('good_id')
        if not good_id:
            return

        # Проверка: новый или существующий
        existing = session.query(Product).filter_by(good_id=good_id).first()
        if existing:
            if existing.is_deleted:
                existing.is_deleted = False
            stats.updated_products += 1
        else:
            stats.new_products += 1

        # 1. Продукт
        gtin = self._extract_main_gtin(data.get('identified_by', []))
        tnved = self._extract_tnved(data.get('good_attrs', []))
        
        product = session.merge(Product(
            good_id=good_id,
            gtin=gtin,
            good_name=data.get('good_name'),
            tnved=tnved,
            brand_id=data.get('brand_id'),
            brand_name=data.get('brand_name'),
            good_status=data.get('good_status'),
            good_detailed_status=json.dumps(data.get('good_detailed_status', [])),
            is_sim=data.get('is_sim', False),
            is_kit=data.get('is_kit', False),
            is_set=data.get('is_set', False),
            good_img=data.get('good_img'),
            good_signed=data.get('good_signed', False),
            good_mark_flag=data.get('good_mark_flag', False),
            good_turn_flag=data.get('good_turn_flag', False),
            flags_updated_date=self._parse_dt(data.get('flags_updated_date')),
            create_date=self._parse_dt(data.get('create_date')),
            update_date=self._parse_dt(data.get('update_date')),
            first_sign_date=self._parse_dt(data.get('first_sign_date')),
            producer_inn=data.get('producer_inn'),
            producer_name=data.get('producer_name'),
            remainder_type=data.get('remainder_type'),
            is_tech_gtin=data.get('is_tech_gtin', False),
            etag=data.get('_batch_etag'),
            last_sync_at=datetime.now(),
            is_deleted=False
        ))
        
        session.flush()

        # 2. Атрибуты (EAV) - DELETE + INSERT
        session.execute(delete(ProductAttribute).where(ProductAttribute.good_id == good_id))
        
        for attr in data.get('good_attrs', []):
            pa = ProductAttribute(
                good_id=good_id,
                attr_id=attr.get('attr_id'),
                attr_name=attr.get('attr_name', ''),
                attr_value=attr.get('attr_value'),
                attr_value_id=attr.get('attr_value_id'),
                attr_value_type=attr.get('attr_value_type'),
                attr_group_id=attr.get('attr_group_id'),
                attr_group_name=attr.get('attr_group_name'),
                level=attr.get('level'),
                gtin=attr.get('gtin'),
                multiplier=attr.get('multiplier'),
                certificate_number=attr.get('certificate_number'),
                certificate_issued_date=self._parse_dt(attr.get('certificate_issued_date')),
                certificate_valid_until_date=self._parse_dt(attr.get('certificate_valid_until_date')),
                certificate_applicant=attr.get('certificate_applicant'),
                certificate_manufacturer=attr.get('certificate_manufacturer'),
                certificate_product_description=attr.get('certificate_product_description'),
            )
            session.add(pa)

        # 3. Упаковки
        session.execute(delete(ProductPackage).where(ProductPackage.good_id == good_id))
        
        # Словарь атрибутов для поиска весогабаритов по GTIN
        attrs_by_gtin = {}
        for attr in data.get('good_attrs', []):
            if attr.get('gtin'):
                if attr['gtin'] not in attrs_by_gtin:
                    attrs_by_gtin[attr['gtin']] = []
                attrs_by_gtin[attr['gtin']].append(attr)

        for ident in data.get('identified_by', []):
            pkg = ProductPackage(
                good_id=good_id,
                identifier_type=ident.get('type', 'gtin'),
                identifier_value=ident.get('value'),
                level=ident.get('level'),
                multiplier=ident.get('multiplier', 1),
                gtin=ident.get('gtin'),
            )
            
            # Заполняем весогабариты если есть匹配 GTIN
            pkg_gtin = ident.get('gtin')
            if pkg_gtin and pkg_gtin in attrs_by_gtin:
                for attr in attrs_by_gtin[pkg_gtin]:
                    aid = attr.get('attr_id')
                    val = attr.get('attr_value')
                    if aid == 2437: pkg.height = val
                    elif aid == 2438: pkg.depth = val
                    elif aid == 2439: pkg.width = val
                    elif aid == 2440: pkg.weight_gross = val
                    elif aid == 13756: pkg.volume = val
                    elif aid == 2710: pkg.package_type = val
                    elif aid == 2713: pkg.material = val
            
            session.add(pkg)

        # 4. Наборы
        session.execute(delete(ProductSetItem).where(ProductSetItem.parent_good_id == good_id))
        
        if data.get('is_set'):
            for item in data.get('set_gtins', []):
                psi = ProductSetItem(
                    parent_good_id=good_id,
                    child_gtin=item.get('gtin'),
                    quantity=item.get('quantity', 1)
                )
                session.add(psi)

        # 5. Изображения
        session.execute(delete(ProductImage).where(ProductImage.good_id == good_id))
        
        for img in data.get('good_images', []):
            pi = ProductImage(
                good_id=good_id,
                photo_type=img.get('photo_type'),
                photo_url=img.get('photo_url'),
                photo_date=self._parse_dt(img.get('photo_date')),
                barcode=img.get('barcode')
            )
            session.add(pi)

        # Commit для одного товара
        session.commit()

    def _handle_rate_limit(self):
        """Проверяет лимиты и делает sleep при необходимости."""
        left = self.client.api_usage_left
        if left is None:
            # Если информация о лимитах недоступна, просто пропускаем проверку
            return
        if left < 5:
            logger.warning(f"Мало запросов ({left}). Сон 10с.")
            time.sleep(10.0)
        elif left < 20:
            logger.debug(f"Запросов осталось {left}. Сон 2с.")
            time.sleep(2.0)

    # --- Helpers ---

    def _extract_main_gtin(self, identified_by: List[Dict]) -> Optional[str]:
        if not identified_by:
            return None
        # Приоритет: trade-unit + gtin
        for item in identified_by:
            if item.get('level') == 'trade-unit' and item.get('type') == 'gtin':
                return item.get('value')
        # Fallback: первый gtin
        for item in identified_by:
            if item.get('type') == 'gtin':
                return item.get('value')
        return None

    def _extract_tnved(self, good_attrs: List[Dict]) -> Optional[str]:
        if not good_attrs:
            return None
        # Приоритет: 13933 (Код ТНВЭД)
        # Затем: 3959 (Группа ТНВЭД)
        target_ids = [13933, 3959]
        found = None
        for attr in good_attrs:
            if attr.get('attr_id') in target_ids:
                val = attr.get('attr_value')
                if val:
                    found = val
                    if attr.get('attr_id') == 13933:
                        return val # Сразу возвращаем точный код
        return found

    def _parse_dt(self, val: Optional[str]) -> Optional[datetime]:
        if not val:
            return None
        try:
            return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def reset_progress(self):
        """Сбрасывает прогресс синхронизации в sync_state."""
        with self.session_factory() as session:
            keys_to_delete = [
                "last_full_sync",
                "remote_etags_snapshot",
                "product_list_progress",
                "product_list_last_run"
            ]
            for key in keys_to_delete:
                self.state_repo.set_state(key, None)
            session.commit()
        logger.info("Прогресс синхронизации сброшен.")
