"""
Модуль конфигурации для интеграции с Национальным Каталогом.

Загружает настройки из переменных окружения и .env файла.
Использует pydantic-settings для валидации и типизации.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Настройки приложения для работы с API Национального Каталога.
    
    Attributes:
        api_key: Ключ доступа API (обязательный)
        api_url: URL API Национального Каталога
        database_url: URL подключения к базе данных SQLite
        log_level: Уровень логирования
        batch_size: Размер батча для загрузки товаров (максимум 25 для feed-product)
        max_retries: Максимальное количество попыток при ошибке сервера
        auth_method: Метод аутентификации ('apikey' или 'bearer')
        bearer_token: Токен для аутентификации Bearer (если используется)
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Обязательные настройки
    api_key: str = Field(
        ...,
        description="API ключ для Национального Каталога"
    )
    
    # Опциональные настройки с дефолтами
    api_url: str = Field(
        default="https://апи.национальный-каталог.рф",
        description="URL API Национального Каталога"
    )
    
    database_url: str = Field(
        default="sqlite:///catalog.db",
        description="URL подключения к базе данных"
    )
    
    log_level: str = Field(
        default="INFO",
        description="Уровень логирования (DEBUG, INFO, WARNING, ERROR)"
    )
    
    batch_size: int = Field(
        default=25,
        ge=1,
        le=25,
        description="Размер батча для загрузки товаров (максимум 25)"
    )
    
    max_retries: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Максимальное количество попыток при ошибке сервера"
    )
    
    auth_method: str = Field(
        default="apikey",
        pattern="^(apikey|bearer)$",
        description="Метод аутентификации: 'apikey' или 'bearer'"
    )
    
    bearer_token: Optional[str] = Field(
        default=None,
        description="Токен для аутентификации Bearer (если auth_method='bearer')"
    )
    
    @property
    def base_url(self) -> str:
        """Возвращает базовый URL API с версией v4."""
        return f"{self.api_url.rstrip('/')}/v4"
    
    @property
    def base_url_v3(self) -> str:
        """Возвращает базовый URL API с версией v3."""
        return f"{self.api_url.rstrip('/')}/v3"
    
    def validate_auth(self) -> None:
        """
        Проверяет корректность настроек аутентификации.
        
        Raises:
            ValueError: Если настройки аутентификации некорректны
        """
        if self.auth_method == "bearer" and not self.bearer_token:
            raise ValueError(
                "bearer_token обязателен при auth_method='bearer'"
            )
        if not self.api_key and self.auth_method == "apikey":
            raise ValueError(
                "api_key обязателен при auth_method='apikey'"
            )


# Глобальный экземпляр настроек
settings = Settings()
