"""
Клиент для работы с API Национального Каталога маркированных товаров (Честный Знак).

Версия: 2.0 (Фаза 1 - рефакторинг с поддержкой ETag, retry, rate limiting)

Особенности:
- Поддержка двух методов аутентификации: apikey (query) и Bearer token (header)
- Универсальный метод request() с возвратом структурированного NKResponse
- ETag для инкрементального обновления (методы: product, short-product, categories, brands)
- Retry-логика с экспоненциальным backoff на 5xx ошибки
- Обработка HTTP 429 с чтением Retry-After (пауза 5 минут если заголовок отсутствует)
- Rate-limit aware: чтение заголовков API-Usage-Limit и API-Method-Usage-Limit
- Логирование всех запросов с указанием endpoint, status, usage counters
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
from enum import Enum

import requests
from requests.adapters import HTTPAdapter, Retry

from config import Settings, settings


# ============================================================================
# ИСКЛЮЧЕНИЯ
# ============================================================================


class NKApiError(Exception):
    """Базовое исключение для ошибок API Национального Каталога."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
    
    def __str__(self) -> str:
        if self.status_code:
            return f"NKApiError {self.status_code}: {self.message}"
        return f"NKApiError: {self.message}"


class NKAuthenticationError(NKApiError):
    """Ошибка аутентификации (401 Unauthorized)."""
    pass


class NKAccessDeniedError(NKApiError):
    """Ошибка доступа (403 Forbidden)."""
    pass


class NKNotFoundError(NKApiError):
    """Ресурс не найден (404 Not Found)."""
    pass


class NKRequestTooLargeError(NKApiError):
    """Размер запроса превышает лимит (413 Payload Too Large)."""
    pass


class NKRateLimitError(NKApiError):
    """Превышен лимит запросов (429 Too Many Requests)."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after
    
    def __str__(self) -> str:
        if self.retry_after:
            return f"NKRateLimitError: {self.message}. Retry after {self.retry_after} seconds"
        return f"NKRateLimitError: {self.message}"


class NKServerError(NKApiError):
    """Ошибка сервера (5xx)."""
    pass


# ============================================================================
# СТРУКТУРА ОТВЕТА
# ============================================================================


@dataclass
class NKResponse:
    """
    Структурированный ответ от API Национального Каталога.
    
    Attributes:
        data: JSON ответа (None при 304 Not Modified)
        etag: Новый ETag из заголовка ответа
        modified: True если данные изменились (не 304)
        status_code: HTTP статус код
        api_usage_left: Осталось запросов в рамках общего лимита
        method_usage_left: Осталось запросов в рамках лимита метода
        headers: Все заголовки ответа (для отладки)
    """
    
    data: Optional[Dict[str, Any]] = None
    etag: Optional[str] = None
    modified: bool = True
    status_code: int = 200
    api_usage_left: Optional[int] = None
    method_usage_left: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    
    @property
    def is_not_modified(self) -> bool:
        """Проверяет, является ли ответ 304 Not Modified."""
        return self.status_code == 304 or not self.modified
    
    @property
    def has_data(self) -> bool:
        """Проверяет, есть ли данные в ответе."""
        return self.data is not None and len(self.data) > 0


# ============================================================================
# КЛИЕНТ API
# ============================================================================


class NKCatalogClient:
    """
    Клиент для работы с API Национального Каталога.
    
    Поддерживает:
    - Два метода аутентификации: apikey (query) и Bearer token (header)
    - ETag для инкрементального обновления
    - Retry-логику с экспоненциальным backoff на 5xx
    - Обработку HTTP 429 с чтением Retry-After
    - Rate-limit aware поведение
    
    Пример использования:
        >>> client = NKCatalogClient()
        >>> response = client.request("GET", "/v3/product", params={"good_id": 123})
        >>> if response.has_data:
        ...     print(response.data)
    """
    
    # Методы, поддерживающие ETag (согласно документации v.5.59 раздел 2.4)
    ETAG_SUPPORTED_METHODS = {"product", "short-product", "categories", "brands"}
    
    # Коды ошибок для retry
    RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
    
    # Максимальное время ожидания между retry (секунды)
    MAX_RETRY_DELAY = 60.0
    
    # Базовая задержка для экспоненциального backoff (секунды)
    BASE_RETRY_DELAY = 1.0
    
    # Порог для preemptive rate limiting (когда осталось мало запросов)
    RATE_LIMIT_THRESHOLD = 10
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        auth_method: str = "apikey",
        bearer_token: Optional[str] = None,
        max_retries: int = 5,
        batch_size: int = 25,
    ):
        """
        Инициализация клиента API.
        
        Args:
            api_key: Ключ доступа API. Если не указан, берётся из настроек.
            api_url: URL API. Если не указан, берётся из настроек.
            auth_method: Метод аутентификации ('apikey' или 'bearer').
            bearer_token: Токен для Bearer аутентификации (если auth_method='bearer').
            max_retries: Максимальное количество попыток при ошибке сервера.
            batch_size: Размер батча для загрузки товаров (максимум 25).
        """
        # Загружаем настройки
        self._settings = Settings()
        
        # Переопределяем настройки переданными параметрами
        self.api_key = api_key or self._settings.api_key
        self.api_url = api_url or self._settings.api_url
        self.auth_method = auth_method or self._settings.auth_method
        self.bearer_token = bearer_token or self._settings.bearer_token
        self.max_retries = max_retries or self._settings.max_retries
        self.batch_size = min(batch_size, 25)  # Максимум 25 для feed-product
        
        # Валидируем аутентификацию
        self._validate_auth()
        
        # Настраиваем базовые URL
        self.base_url_v4 = f"{self.api_url.rstrip('/')}/v4"
        self.base_url_v3 = f"{self.api_url.rstrip('/')}/v3"
        
        # Создаём сессию с connection pooling
        self.session = self._create_session()
        
        # Настраиваем логгер
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(getattr(logging, self._settings.log_level.upper(), logging.INFO))
        
        # Счётчики rate limiting
        self._api_usage_left: Optional[int] = None
        self._method_usage_left: Optional[int] = None
        
        self.logger.info(
            f"Инициализирован NKCatalogClient: url={self.api_url}, "
            f"auth={self.auth_method}, batch_size={self.batch_size}"
        )
    
    @property
    def api_usage_left(self) -> Optional[int]:
        """Возвращает количество оставшихся запросов в рамках общего лимита."""
        return self._api_usage_left
    
    @property
    def method_usage_left(self) -> Optional[int]:
        """Возвращает количество оставшихся запросов в рамках лимита метода."""
        return self._method_usage_left
    
    def _validate_auth(self) -> None:
        """
        Проверяет корректность настроек аутентификации.
        
        Raises:
            ValueError: Если настройки аутентификации некорректны
        """
        if self.auth_method == "bearer" and not self.bearer_token:
            raise ValueError("bearer_token обязателен при auth_method='bearer'")
        if self.auth_method == "apikey" and not self.api_key:
            raise ValueError("api_key обязателен при auth_method='apikey'")
    
    def _create_session(self) -> requests.Session:
        """
        Создаёт и настраивает requests.Session с connection pooling и retry.
        
        Returns:
            Настроенная сессия requests
        """
        session = requests.Session()
        
        # Настраиваем retry стратегию
        retry_strategy = Retry(
            total=0,  # Мы обрабатываем retry вручную для большего контроля
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            backoff_factor=1.0,
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
            pool_block=False,
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Устанавливаем таймауты
        session.timeout = 30  # 30 секунд на запрос
        
        return session
    
    def _get_auth_params(self) -> Dict[str, Any]:
        """
        Возвращает параметры аутентификации для запроса.
        
        Returns:
            Словарь с параметрами аутентификации
        """
        if self.auth_method == "apikey":
            return {"apikey": self.api_key}
        return {}
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """
        Возвращает заголовки аутентификации для запроса.
        
        Returns:
            Словарь с заголовками аутентификации
        """
        headers = {}
        if self.auth_method == "bearer" and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers
    
    def _parse_usage_limit(self, header_value: str) -> Optional[int]:
        """
        Парсит заголовок API-Usage-Limit формата "1/500".
        
        Args:
            header_value: Значение заголовка (например, "1/500")
            
        Returns:
            Количество оставшихся запросов или None если не удалось распарсить
        """
        try:
            parts = header_value.split("/")
            if len(parts) == 2:
                used = int(parts[0])
                total = int(parts[1])
                return max(0, total - used)
        except (ValueError, IndexError):
            self.logger.warning(f"Не удалось распарсить API-Usage-Limit: {header_value}")
        return None
    
    def _handle_rate_limit(self, api_usage: Optional[int], method_usage: Optional[int]) -> None:
        """
        Обрабатывает rate limiting, делая паузу при приближении к лимиту.
        
        Args:
            api_usage: Оставшиеся запросы в рамках общего лимита
            method_usage: Оставшиеся запросы в рамках лимита метода
        """
        # Сохраняем значения для будущего использования
        self._api_usage_left = api_usage
        self._method_usage_left = method_usage
        
        # Проверяем общий лимит
        if api_usage is not None and api_usage <= 0:
            self.logger.warning("Общий лимит API исчерпан. Ожидание 5 минут...")
            time.sleep(300)  # 5 минут согласно документации
            return
        
        # Проверяем лимит метода
        if method_usage is not None and method_usage <= 0:
            self.logger.warning("Лимит метода исчерпан. Ожидание 5 минут...")
            time.sleep(300)
            return
        
        # Preemptive замедление при приближении к лимиту
        if api_usage is not None and api_usage <= self.RATE_LIMIT_THRESHOLD:
            self.logger.info(f"Мало запросов осталось ({api_usage}). Замедление...")
            time.sleep(1.0)
        
        if method_usage is not None and method_usage <= self.RATE_LIMIT_THRESHOLD:
            self.logger.info(f"Мало запросов метода осталось ({method_usage}). Замедление...")
            time.sleep(1.0)
    
    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        etag: Optional[str] = None,
        use_etag: bool = True,
        timeout: int = 30,
    ) -> NKResponse:
        """
        Универсальный метод для выполнения запросов к API.
        
        Args:
            method: HTTP метод ("GET" или "POST")
            endpoint: Относительный путь endpoint (например, "/v3/product")
            params: Query параметры запроса
            json_data: JSON тело для POST запросов
            etag: Предыдущий ETag для проверки изменений (If-None-Match)
            use_etag: Использовать ли ETag (True для методов, поддерживающих ETag)
            timeout: Таймаут запроса в секундах
            
        Returns:
            NKResponse со структурированными данными ответа
            
        Raises:
            NKAuthenticationError: При ошибке аутентификации (401)
            NKAccessDeniedError: При отсутствии доступа (403)
            NKNotFoundError: При отсутствии ресурса (404)
            NKRequestTooLargeError: При превышении размера запроса (413)
            NKRateLimitError: При превышении лимита запросов (429)
            NKServerError: При ошибке сервера (5xx)
            NKApiError: При других ошибках
        """
        # Формируем полный URL
        if endpoint.startswith("/v3") or endpoint.startswith("/v4"):
            url = f"{self.api_url.rstrip('/')}{endpoint}"
        else:
            url = f"{self.base_url_v4}{endpoint}"
        
        # Подготовка параметров
        request_params = self._get_auth_params()
        if params:
            request_params.update(params)
        
        # Подготовка заголовков
        headers = self._get_auth_headers()
        headers["Accept"] = "application/json"
        
        # Добавляем ETag если указан и метод поддерживает
        if etag and use_etag:
            headers["If-None-Match"] = etag
            self.logger.debug(f"Добавлен If-None-Match: {etag}")
        
        # Проверка rate limiting перед запросом
        self._handle_rate_limit(self._api_usage_left, self._method_usage_left)
        
        # Выполнение запроса с retry
        attempt = 0
        last_error: Optional[Exception] = None
        
        while attempt <= self.max_retries:
            try:
                self.logger.debug(
                    f"Запрос #{attempt + 1}: {method} {url}, "
                    f"params={request_params}, headers={headers}"
                )
                
                # Выполняем запрос
                response = self.session.request(
                    method=method.upper(),
                    url=url,
                    params=request_params,
                    json=json_data,
                    headers=headers,
                    timeout=timeout,
                )
                
                # Парсим заголовки rate limiting
                api_usage_header = response.headers.get("API-Usage-Limit")
                method_usage_header = response.headers.get("API-Method-Usage-Limit")
                
                api_usage_left = self._parse_usage_limit(api_usage_header) if api_usage_header else None
                method_usage_left = self._parse_usage_limit(method_usage_header) if method_usage_header else None
                
                # Логируем ответ
                self.logger.info(
                    f"Ответ: {response.status_code}, "
                    f"usage={api_usage_header or 'N/A'}, "
                    f"method_usage={method_usage_header or 'N/A'}"
                )
                
                # Обработка успешных ответов
                if response.status_code in (200, 304):
                    # Извлекаем ETag из ответа
                    response_etag = response.headers.get("ETag")
                    
                    # При 304 возвращаем пустой ответ
                    if response.status_code == 304:
                        self.logger.info("ETag совпал, данные не изменились (304 Not Modified)")
                        return NKResponse(
                            data=None,
                            etag=response_etag,
                            modified=False,
                            status_code=304,
                            api_usage_left=api_usage_left,
                            method_usage_left=method_usage_left,
                            headers=dict(response.headers),
                        )
                    
                    # Парсим JSON
                    try:
                        data = response.json()
                    except ValueError as e:
                        self.logger.error(f"Ошибка парсинга JSON: {e}")
                        data = None
                    
                    return NKResponse(
                        data=data,
                        etag=response_etag,
                        modified=True,
                        status_code=200,
                        api_usage_left=api_usage_left,
                        method_usage_left=method_usage_left,
                        headers=dict(response.headers),
                    )
                
                # Обработка ошибок
                self._handle_error_response(response)
                
            except requests.exceptions.ConnectionError as e:
                last_error = e
                self.logger.warning(f"ConnectionError (попытка {attempt + 1}/{self.max_retries}): {e}")
                
            except requests.exceptions.Timeout as e:
                last_error = e
                self.logger.warning(f"Timeout (попытка {attempt + 1}/{self.max_retries}): {e}")
            
            except NKRequestTooLargeError:
                # 413 не ретраим
                raise
            
            except NKRateLimitError:
                # 429 не ретраим в цикле, обработка внутри _handle_error_response
                raise
            
            except (NKAuthenticationError, NKAccessDeniedError, NKNotFoundError):
                # 4xx не ретраим
                raise
            
            except NKServerError as e:
                last_error = e
                self.logger.warning(f"ServerError (попытка {attempt + 1}/{self.max_retries}): {e}")
            
            # Экспоненциальный backoff
            if attempt < self.max_retries:
                delay = min(self.BASE_RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY)
                self.logger.info(f"Retry через {delay:.1f} сек...")
                time.sleep(delay)
            
            attempt += 1
        
        # Все попытки исчерпаны
        error_msg = f"Все {self.max_retries} попыток исчерпаны"
        if last_error:
            error_msg += f": {last_error}"
        
        raise NKServerError(error_msg)
    
    def _handle_error_response(self, response: requests.Response) -> None:
        """
        Обрабатывает ошибочные HTTP ответы.
        
        Args:
            response: Объект ответа requests
            
        Raises:
            Соответствующее исключение в зависимости от статуса
        """
        status_code = response.status_code
        error_data = None
        
        try:
            error_data = response.json()
        except ValueError:
            error_data = {"error": response.text}
        
        error_message = error_data.get("error", error_data.get("message", str(error_data))) if error_data else f"HTTP {status_code}"
        
        # 400 Bad Request
        if status_code == 400:
            raise NKApiError(f"Ошибка в параметрах запроса: {error_message}", status_code=status_code, response_data=error_data)
        
        # 401 Unauthorized
        if status_code == 401:
            raise NKAuthenticationError(f"Ошибка аутентификации: {error_message}", status_code=status_code, response_data=error_data)
        
        # 403 Forbidden
        if status_code == 403:
            raise NKAccessDeniedError(f"Нет доступа: {error_message}", status_code=status_code, response_data=error_data)
        
        # 404 Not Found
        if status_code == 404:
            raise NKNotFoundError(f"Ресурс не найден: {error_message}", status_code=status_code, response_data=error_data)
        
        # 413 Payload Too Large
        if status_code == 413:
            raise NKRequestTooLargeError(f"Размер запроса превышает лимит: {error_message}", status_code=status_code, response_data=error_data)
        
        # 429 Too Many Requests
        if status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_after_sec = None
            
            if retry_after:
                try:
                    retry_after_sec = int(retry_after)
                except ValueError:
                    pass
            
            if retry_after_sec is None:
                # Согласно документации v.5.59 раздел 2.1: пауза 5 минут
                retry_after_sec = 300
                self.logger.warning("Retry-After не указан, используем 5 минут по умолчанию")
            
            raise NKRateLimitError(f"Превышен лимит запросов: {error_message}", retry_after=retry_after_sec)
        
        # 5xx Server Error
        if status_code >= 500:
            raise NKServerError(f"Ошибка сервера {status_code}: {error_message}", status_code=status_code, response_data=error_data)
        
        # Другие ошибки
        raise NKApiError(f"Неизвестная ошибка {status_code}: {error_message}", status_code=status_code, response_data=error_data)
    
    # ========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ С ТОВАРАМИ
    # ========================================================================
    
    def get_feed_product(
        self,
        good_ids: Optional[List[int]] = None,
        gtins: Optional[List[str]] = None,
        subaccount: bool = False,
    ) -> NKResponse:
        """
        Получение полной информации о собственных карточках товаров.
        
        Метод возвращает всю имеющуюся информацию о карточке товара,
        включая все заполненные атрибуты независимо от статуса.
        
        Args:
            good_ids: Список идентификаторов товаров (максимум 25)
            gtins: Список GTIN товаров (максимум 25)
            subaccount: Запрашивать ли данные из субаккаунтов
            
        Returns:
            NKResponse с данными товаров
            
        Raises:
            ValueError: Если не указаны good_ids или gtins
            NKRequestTooLargeError: Если передано больше 25 товаров
        """
        if not good_ids and not gtins:
            raise ValueError("Необходимо указать good_ids или gtins")
        
        if good_ids and len(good_ids) > 25:
            raise NKRequestTooLargeError("Максимум 25 good_ids за запрос")
        
        if gtins and len(gtins) > 25:
            raise NKRequestTooLargeError("Максимум 25 gtins за запрос")
        
        params = {"subaccount": str(subaccount).lower()}
        
        if good_ids:
            params["good_ids"] = ";".join(map(str, good_ids))
        
        if gtins:
            params["gtins"] = ";".join(gtins)
        
        # feed-product не поддерживает ETag
        return self.request("GET", "/v3/feed-product", params=params, use_etag=False)
    
    def get_product(
        self,
        good_id: Optional[int] = None,
        gtin: Optional[str] = None,
        cached_etag: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение информации о карточке товара (только опубликованные и архивные).
        
        Поддерживает ETag для инкрементального обновления.
        
        Args:
            good_id: Идентификатор товара в НК
            gtin: GTIN товара
            cached_etag: Сохранённый ранее ETag для проверки изменений
            
        Returns:
            NKResponse с данными товара
            
        Note:
            Согласно документации, метод возвращает только опубликованные и архивные карточки.
            Для черновиков используйте get_feed_product().
        """
        params = {}
        
        if good_id:
            params["good_id"] = str(good_id)
        elif gtin:
            params["gtin"] = gtin
        else:
            raise ValueError("Необходимо указать good_id или gtin")
        
        return self.request("GET", "/v3/product", params=params, etag=cached_etag, use_etag=True)
    
    def get_product_list(
        self,
        from_date: str = "2020-01-01 00:00:00",
        to_date: str = "2026-12-31 23:59:59",
        limit: int = 1000,
        offset: int = 0,
        good_status: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение списка товаров (краткая информация).
        
        Args:
            from_date: Дата начала периода
            to_date: Дата окончания периода
            limit: Количество записей (максимум 1000)
            offset: Смещение для пагинации
            good_status: Фильтр по статусу (draft/published/archived)
            
        Returns:
            NKResponse со списком товаров
            
        Note:
            Метод имеет ограничение: максимум 10 000 записей за период.
            Для больших объёмов необходимо нарезать по датам.
        """
        params = {
            "from_date": from_date,
            "to_date": to_date,
            "limit": min(limit, 1000),
            "offset": offset,
        }
        
        if good_status:
            params["good_status"] = good_status
        
        # product-list не поддерживает ETag
        return self.request("GET", "/v4/product-list", params=params, use_etag=False)
    
    # ========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ СО СПРАВОЧНИКАМИ
    # ========================================================================
    
    def get_categories(
        self,
        gismt_code: Optional[str] = None,
        tnved: Optional[str] = None,
        cached_etag: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение дерева категорий.
        
        Args:
            gismt_code: Фильтр по коду товарной группы ГИС МТ
            tnved: Фильтр по коду ТН ВЭД
            cached_etag: Сохранённый ранее ETag
            
        Returns:
            NKResponse со списком категорий
        """
        params = {}
        
        if gismt_code:
            params["gismt_code"] = gismt_code
        
        if tnved:
            params["tnved"] = tnved
        
        return self.request("GET", "/v3/categories", params=params, etag=cached_etag, use_etag=True)
    
    def get_brands(
        self,
        limit: int = 10000,
        offset: int = 0,
        cached_etag: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение справочника брендов.
        
        Args:
            limit: Количество записей (максимум 10000)
            offset: Смещение для пагинации
            cached_etag: Сохранённый ранее ETag
            
        Returns:
            NKResponse со списком брендов
        """
        params = {
            "limit": min(limit, 10000),
            "offset": offset,
        }
        
        return self.request("GET", "/v3/brands", params=params, etag=cached_etag, use_etag=True)
    
    def get_countries(self, cached_etag: Optional[str] = None) -> NKResponse:
        """
        Получение справочника стран (ISO Alpha-2).
        
        Args:
            cached_etag: Сохранённый ранее ETag
            
        Returns:
            NKResponse со списком стран
        """
        return self.request("GET", "/v3/dictionary/isocountry", etag=cached_etag, use_etag=True)
    
    def get_attributes(
        self,
        cat_id: Optional[int] = None,
        tnved: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение атрибутивной модели для категории или ТН ВЭД.
        
        Args:
            cat_id: Идентификатор категории
            tnved: Код ТН ВЭД
            
        Returns:
            NKResponse со списком атрибутов
            
        Raises:
            ValueError: Если не указан ни cat_id ни tnved
        """
        if not cat_id and not tnved:
            raise ValueError("Необходимо указать cat_id или tnved")
        
        params = {}
        
        if cat_id:
            params["cat_id"] = str(cat_id)
        
        if tnved:
            params["tnved"] = tnved
        
        # attributes не поддерживает ETag
        return self.request("GET", "/v3/attributes", params=params, use_etag=False)
    
    # ========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ С РАЗРЕШИТЕЛЬНЫМИ ДОКУМЕНТАМИ
    # ========================================================================
    
    def get_rd_info_by_gtin(
        self,
        gtin: str,
        inn: Optional[str] = None,
    ) -> NKResponse:
        """
        Получение информации о разрешительных документах по GTIN.
        
        Args:
            gtin: Код товара
            inn: ИНН производителя/импортёра (опционально)
            
        Returns:
            NKResponse со списком документов
        """
        json_data = {"gtin": gtin}
        
        if inn:
            json_data["inn"] = inn
        
        return self.request("POST", "/v4/rd-info-by-gtin", json_data=json_data, use_etag=False)
    
    def get_rd_info(
        self,
        documents: List[Dict[str, Any]],
    ) -> NKResponse:
        """
        Получение информации о разрешительных документах по номеру и дате.
        
        Args:
            documents: Список документов с полями number, from_date, attr_id
                      (максимум 25 документов за запрос)
                      
        Returns:
            NKResponse с информацией о документах
            
        Raises:
            NKRequestTooLargeError: Если передано больше 25 документов
        """
        if len(documents) > 25:
            raise NKRequestTooLargeError("Максимум 25 документов за запрос")
        
        return self.request("POST", "/v4/rd-info", json_data={"documents": documents}, use_etag=False)
    
    # ========================================================================
    # МЕТОДЫ ДЛЯ ИНКРЕМЕНТАЛЬНОГО ОБНОВЛЕНИЯ
    # ========================================================================
    
    def get_etags_list(self) -> NKResponse:
        """
        Получение списка good_id + etag всех своих товаров.
        
        Используется для выявления изменений и инкрементального обновления.
        
        Returns:
            NKResponse со списком {good_id, etag}
        """
        return self.request("GET", "/v3/etagslist", use_etag=False)


# ============================================================================
# ТОЧКА ВХОДА ДЛЯ ТЕСТИРОВАНИЯ
# ============================================================================

if __name__ == "__main__":
    # Пример использования
    logging.basicConfig(level=logging.INFO)
    
    try:
        client = NKCatalogClient()
        print(f"Клиент успешно инициализирован: {client.api_url}")
        print(f"Метод аутентификации: {client.auth_method}")
        print(f"Batch size: {client.batch_size}")
    except Exception as e:
        print(f"Ошибка инициализации: {e}")
