"""
Репозиторий для работы с таблицей sync_state.

Хранит состояние синхронизации: ETag, курсоры, метаданные.
Используется для инкрементального обновления справочников.
"""

from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from models import SyncState


class SyncStateRepo:
    """
    Репозиторий для управления состоянием синхронизации.
    
    Пример использования:
        >>> repo = SyncStateRepo(session)
        >>> state = repo.get_state("categories_etag")
        >>> repo.set_state("categories_etag", {"etag": "abc123", "updated_at": "..."})
    """
    
    def __init__(self, session: Session):
        """
        Инициализация репозитория.
        
        Args:
            session: SQLAlchemy сессия
        """
        self.session = session
    
    def get_state(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Получает состояние по ключу.
        
        Args:
            key: Ключ состояния (например, "categories_etag")
            
        Returns:
            Словарь со значением или None если не найдено
        """
        state = self.session.query(SyncState).filter_by(key=key).first()
        if state is None:
            return None
        return state.value
    
    def set_state(self, key: str, value: Dict[str, Any]) -> None:
        """
        Устанавливает состояние по ключу.
        
        Если запись существует — обновляет, иначе создаёт новую.
        
        Args:
            key: Ключ состояния
            value: Значение (словарь)
        """
        state = self.session.query(SyncState).filter_by(key=key).first()
        
        if state is None:
            state = SyncState(key=key, value=value)
            self.session.add(state)
        else:
            state.value = value
            state.updated_at = datetime.utcnow()
        
        self.session.commit()
    
    def delete_state(self, key: str) -> bool:
        """
        Удаляет состояние по ключу.
        
        Args:
            key: Ключ состояния
            
        Returns:
            True если запись была удалена, False если не найдена
        """
        state = self.session.query(SyncState).filter_by(key=key).first()
        if state is None:
            return False
        
        self.session.delete(state)
        self.session.commit()
        return True
    
    def get_etag(self, key: str) -> Optional[str]:
        """
        Получает ETag из состояния.
        
        Args:
            key: Ключ состояния (например, "categories_etag")
            
        Returns:
            ETag строка или None
        """
        state = self.get_state(key)
        if state is None:
            return None
        return state.get("etag")
    
    def set_etag(self, key: str, etag: str) -> None:
        """
        Сохраняет ETag в состоянии.
        
        Args:
            key: Ключ состояния
            etag: ETag значение
        """
        value = {
            "etag": etag,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.set_state(key, value)
    
    def has_changed(self, key: str, current_etag: Optional[str]) -> bool:
        """
        Проверяет, изменились ли данные по сравнению с сохранённым ETag.
        
        Args:
            key: Ключ состояния
            current_etag: Текущий ETag от API
            
        Returns:
            True если данные изменились (или ETag не сохранён), False если совпадает
        """
        saved_etag = self.get_etag(key)
        if saved_etag is None:
            return True  # Нет сохранённого ETag — загружаем
        if current_etag is None:
            return True  # API не вернул ETag — загружаем
        return saved_etag != current_etag
