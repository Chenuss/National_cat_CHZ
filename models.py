"""
SQLAlchemy модели для базы данных Национального Каталога.

Отражают схему из db_schema.sql один-в-один.
Используют декларативный базовый класс SQLAlchemy 2.0.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    CheckConstraint,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    relationship,
    Session,
    Mapped,
    mapped_column,
)
from sqlalchemy.dialects.sqlite import JSON


# ============================================================================
# БАЗОВЫЙ КЛАСС
# ============================================================================


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    
    __abstract__ = True
    
    # Общие поля для всех таблиц
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


# ============================================================================
# МОДЕЛЬ: Product - Основная таблица товаров
# ============================================================================


class Product(Base):
    """
    Модель товара из Национального Каталога.
    
    Хранит базовую информацию о товарах. good_id является первичным ключом
    для обеспечения идемпотентности при синхронизации.
    """
    
    __tablename__ = "products"
    
    # Первичный ключ
    good_id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Идентификатор товара в НК")
    
    # Основные поля
    gtin: Mapped[str] = mapped_column(String(14), nullable=False, index=True, comment="GTIN товара (до 14 символов)")
    good_name: Mapped[str] = mapped_column(Text, nullable=False, comment="Наименование товара")
    tnved: Mapped[Optional[str]] = mapped_column(String(20), comment="Код ТН ВЭД")
    
    # Бренд
    brand_name: Mapped[Optional[str]] = mapped_column(Text, index=True, comment="Наименование бренда")
    brand_id: Mapped[Optional[int]] = mapped_column(Integer, comment="Идентификатор бренда в НК")
    
    # Статусы
    good_status: Mapped[Optional[str]] = mapped_column(String(50), index=True, comment="Технологический статус")
    good_detailed_status: Mapped[Optional[List[str]]] = mapped_column(JSON, comment="Массив детальных статусов")
    
    # Признаки типа карточки
    is_kit: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="Признак комплекта")
    is_set: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="Признак набора")
    is_sim: Mapped[bool] = mapped_column(Boolean, default=False, comment="Индустриальная маркировка (префикс 004)")
    is_tech_gtin: Mapped[bool] = mapped_column(Boolean, default=False, comment="Технический GTIN (префикс 029)")
    
    # Флаги и подписания
    good_signed: Mapped[Optional[bool]] = mapped_column(Boolean, comment="Признак подписания")
    good_mark_flag: Mapped[Optional[bool]] = mapped_column(Boolean, comment="Флаг эмиссии КМ")
    good_turn_flag: Mapped[Optional[bool]] = mapped_column(Boolean, comment="Флаг ввода в оборот")
    
    # Производитель/импортер
    producer_inn: Mapped[Optional[str]] = mapped_column(String(12), index=True, comment="ИНН производителя")
    producer_name: Mapped[Optional[str]] = mapped_column(Text, comment="Наименование производителя")
    
    # Даты
    create_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата создания карточки")
    update_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата обновления карточки")
    first_sign_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата первого подписания")
    flags_updated_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата обновления флагов")
    
    # Изображения и ETag
    good_img: Mapped[Optional[str]] = mapped_column(Text, comment="URL фото по умолчанию")
    etag: Mapped[Optional[str]] = mapped_column(String(100), comment="ETag для инкрементального обновления")
    
    # Relationships
    packages: Mapped[List["ProductPackage"]] = relationship(
        "ProductPackage",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    
    attributes: Mapped[List["ProductAttribute"]] = relationship(
        "ProductAttribute",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    
    images: Mapped[List["ProductImage"]] = relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    
    certificates: Mapped[List["Certificate"]] = relationship(
        "Certificate",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    
    set_items: Mapped[List["ProductSetItem"]] = relationship(
        "ProductSetItem",
        back_populates="parent_product",
        cascade="all, delete-orphan",
    )
    
    __table_args__ = (
        Index("idx_products_updated_at", "updated_at"),
    )
    
    
    def __repr__(self) -> str:
        return f"<Product(good_id={self.good_id}, gtin='{self.gtin}', name='{self.good_name}')>"


# ============================================================================
# МОДЕЛЬ: ProductPackage - Уровни упаковки
# ============================================================================


class ProductPackage(Base):
    """
    Модель уровня упаковки товара.
    
    Один товар может иметь несколько упаковок разных уровней:
    trade-unit, inner-pack, box, layer, pallet, metro-unit, show-pack.
    """
    
    __tablename__ = "product_packages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    good_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("products.good_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Идентификатор упаковки
    identifier_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="Тип идентификатора")
    identifier_value: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="Значение идентификатора")
    
    # Уровень и характеристики
    level: Mapped[str] = mapped_column(String(50), nullable=False, index=True, comment="Уровень упаковки")
    multiplier: Mapped[int] = mapped_column(Integer, default=1, comment="Количество товаров в упаковке")
    
    # Габариты (мм)
    width_mm: Mapped[Optional[int]] = mapped_column(Integer, comment="Ширина (мм)")
    height_mm: Mapped[Optional[int]] = mapped_column(Integer, comment="Высота (мм)")
    length_mm: Mapped[Optional[int]] = mapped_column(Integer, comment="Длина (мм)")
    
    # Вес (г)
    weight_gross_g: Mapped[Optional[int]] = mapped_column(Integer, comment="Вес брутто (г)")
    weight_net_g: Mapped[Optional[int]] = mapped_column(Integer, comment="Вес нетто (г)")
    
    # Материал
    material: Mapped[Optional[str]] = mapped_column(String(100), comment="Материал упаковки")
    
    # Relationship
    product: Mapped["Product"] = relationship("Product", back_populates="packages")
    
    __table_args__ = (
        UniqueConstraint("good_id", "level", "identifier_value", name="uq_package_good_level_identifier"),
    )
    
    def __repr__(self) -> str:
        return f"<ProductPackage(id={self.id}, good_id={self.good_id}, level='{self.level}')>"


# ============================================================================
# МОДЕЛЬ: ProductAttribute - Атрибуты товаров (EAV)
# ============================================================================


class ProductAttribute(Base):
    """
    Модель атрибута товара (Entity-Attribute-Value).
    
    Позволяет хранить произвольное количество атрибутов для каждого товара.
    """
    
    __tablename__ = "product_attributes"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    good_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.good_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Атрибут
    attr_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="ID атрибута в НК")
    attr_name: Mapped[Optional[str]] = mapped_column(Text, comment="Наименование атрибута")
    attr_value: Mapped[Optional[str]] = mapped_column(Text, index=True, comment="Значение атрибута")
    
    # Метаданные атрибута
    value_type: Mapped[Optional[str]] = mapped_column(String(20), comment="Тип значения")
    unit: Mapped[Optional[str]] = mapped_column(String(50), comment="Единица измерения")
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, comment="Обязательность")
    is_multiplicable: Mapped[bool] = mapped_column(Boolean, default=False, comment="Мультиплицируемость")
    layer: Mapped[Optional[str]] = mapped_column(String(20), comment="Слой (first_layer/second_layer)")
    
    # Relationship
    product: Mapped["Product"] = relationship("Product", back_populates="attributes")
    
    __table_args__ = (
        UniqueConstraint("good_id", "attr_id", name="uq_product_attr"),
    )
    
    def __repr__(self) -> str:
        return f"<ProductAttribute(good_id={self.good_id}, attr_id={self.attr_id}, value='{self.attr_value}')>"


# ============================================================================
# МОДЕЛЬ: ProductImage - Изображения товаров
# ============================================================================


class ProductImage(Base):
    """Модель изображения товара."""
    
    __tablename__ = "product_images"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    good_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.good_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Информация об изображении
    photo_type: Mapped[Optional[str]] = mapped_column(String(50), index=True, comment="Тип изображения")
    photo_url: Mapped[str] = mapped_column(Text, nullable=False, comment="URL в НК")
    local_path: Mapped[Optional[str]] = mapped_column(Text, comment="Локальный путь")
    size: Mapped[Optional[str]] = mapped_column(String(20), comment="Размер (small/medium/large)")
    width_px: Mapped[Optional[int]] = mapped_column(Integer, comment="Ширина (px)")
    height_px: Mapped[Optional[int]] = mapped_column(Integer, comment="Высота (px)")
    
    # Relationship
    product: Mapped["Product"] = relationship("Product", back_populates="images")
    
    __table_args__ = (
    )
    
    def __repr__(self) -> str:
        return f"<ProductImage(id={self.id}, good_id={self.good_id}, type='{self.photo_type}')>"


# ============================================================================
# МОДЕЛЬ: Certificate - Разрешительные документы
# ============================================================================


class Certificate(Base):
    """
    Модель разрешительного документа (сертификат, декларация, СГР).
    
    Допустимые attr_id:
    - 23561: Сертификат соответствия
    - 23557: Декларация о соответствии
    - 23765: Свидетельство о государственной регистрации (СГР)
    - 23555: Протокол испытаний
    - 23890: Регистрационное удостоверение
    """
    
    __tablename__ = "certificates"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    good_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.good_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    gtin: Mapped[Optional[str]] = mapped_column(String(14), index=True, comment="GTIN товара")
    
    # Тип документа
    attr_id: Mapped[int] = mapped_column(
        Integer, 
        nullable=False,
    )
    
    # Информация о документе
    number: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="Номер документа")
    from_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата выдачи")
    to_date: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Дата окончания")
    status: Mapped[Optional[str]] = mapped_column(String(50), index=True, comment="Статус")
    status_group: Mapped[Optional[str]] = mapped_column(String(50), comment="Группа статуса")
    
    # Продукт
    product_tnved: Mapped[Optional[str]] = mapped_column(String(20), comment="ТН ВЭД продукта")
    applicant: Mapped[Optional[str]] = mapped_column(Text, comment="Заявитель")
    manufacturer: Mapped[Optional[str]] = mapped_column(Text, comment="Производитель")
    product_tech_regulations: Mapped[Optional[str]] = mapped_column(Text, comment="Техрегламенты")
    
    # Relationship
    product: Mapped["Product"] = relationship("Product", back_populates="certificates")
    
    __table_args__ = (
        CheckConstraint(
            "attr_id IN (23561, 23557, 23765, 23555, 23890)",
            name="chk_certificate_attr_id"
        ),
    )
    
    def __repr__(self) -> str:
        return f"<Certificate(id={self.id}, number='{self.number}', attr_id={self.attr_id})>"


# ============================================================================
# МОДЕЛЬ: ProductSetItem - Элементы наборов
# ============================================================================


class ProductSetItem(Base):
    """
    Модель элемента набора (для товаров с is_set=True).
    
    Хранит информацию о вложенных товарах в наборах.
    """
    
    __tablename__ = "product_set_items"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_good_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.good_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Вложенный товар
    item_gtin: Mapped[str] = mapped_column(String(14), nullable=False, index=True, comment="GTIN вложенного товара")
    quantity: Mapped[int] = mapped_column(Integer, default=1, comment="Количество в наборе")
    item_good_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("products.good_id", ondelete="SET NULL"),
    )
    
    # Relationships
    parent_product: Mapped["Product"] = relationship("Product", back_populates="set_items", foreign_keys=[parent_good_id])
    item_product: Mapped[Optional["Product"]] = relationship("Product", foreign_keys=[item_good_id])
    
    __table_args__ = (
        UniqueConstraint("parent_good_id", "item_gtin", name="uq_set_item"),
    )
    
    def __repr__(self) -> str:
        return f"<ProductSetItem(parent={self.parent_good_id}, gtin='{self.item_gtin}', qty={self.quantity})>"


# ============================================================================
# МОДЕЛЬ: Category - Справочник категорий
# ============================================================================


class Category(Base):
    """Модель категории товаров (дерево категорий)."""
    
    __tablename__ = "categories"
    
    cat_id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Идентификатор категории")
    cat_name: Mapped[str] = mapped_column(Text, nullable=False, comment="Наименование категории")
    cat_parent_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("categories.cat_id", ondelete="CASCADE"),
        index=True,
    )
    cat_level: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="Уровень вложенности")
    category_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="Активность")
    gismt_codes: Mapped[Optional[List[str]]] = mapped_column(JSON, comment="Коды товарных групп ГИС МТ")
    tnved_codes: Mapped[Optional[List[str]]] = mapped_column(JSON, comment="Коды ТН ВЭД")
    etag: Mapped[Optional[str]] = mapped_column(String(100), comment="ETag для инкрементального обновления")
    
    # Relationship для дерева
    parent: Mapped[Optional["Category"]] = relationship("Category", remote_side=[cat_id], backref="children")
    
    __table_args__ = (
    )
    
    def __repr__(self) -> str:
        return f"<Category(cat_id={self.cat_id}, name='{self.cat_name}', level={self.cat_level})>"


# ============================================================================
# МОДЕЛЬ: Brand - Справочник брендов
# ============================================================================


class Brand(Base):
    """Модель бренда/товарного знака."""
    
    __tablename__ = "brands"
    
    brand_id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Идентификатор бренда")
    brand_name: Mapped[str] = mapped_column(Text, nullable=False, index=True, comment="Наименование бренда")
    owner_name: Mapped[Optional[str]] = mapped_column(Text, comment="Владелец бренда")
    owner_inn: Mapped[Optional[str]] = mapped_column(String(12), index=True, comment="ИНН владельца")
    country_code: Mapped[Optional[str]] = mapped_column(String(2), index=True, comment="Код страны (ISO Alpha-2)")
    etag: Mapped[Optional[str]] = mapped_column(String(100), comment="ETag для инкрементального обновления")
    
    __table_args__ = (
    )
    
    def __repr__(self) -> str:
        return f"<Brand(brand_id={self.brand_id}, name='{self.brand_name}')>"


# ============================================================================
# МОДЕЛЬ: Country - Справочник стран
# ============================================================================


class Country(Base):
    """Модель страны (ISO 3166-1 Alpha-2)."""
    
    __tablename__ = "countries"
    
    code: Mapped[str] = mapped_column(String(2), primary_key=True, comment="ISO Alpha-2 код")
    name_ru: Mapped[str] = mapped_column(Text, nullable=False, comment="Наименование на русском")
    name_en: Mapped[Optional[str]] = mapped_column(Text, comment="Наименование на английском")
    etag: Mapped[Optional[str]] = mapped_column(String(100), comment="ETag для инкрементального обновления")
    
    __table_args__ = (
    )
    
    def __repr__(self) -> str:
        return f"<Country(code='{self.code}', name='{self.name_ru}')>"


# ============================================================================
# МОДЕЛЬ: AttributeModel - Атрибутивные модели
# ============================================================================


class AttributeModel(Base):
    """Модель атрибута для категории/ТН ВЭД."""
    
    __tablename__ = "attribute_models"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cat_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, comment="ID категории")
    tnved: Mapped[Optional[str]] = mapped_column(String(20), index=True, comment="Код ТН ВЭД")
    attr_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="ID атрибута")
    attr_name: Mapped[str] = mapped_column(Text, nullable=False, comment="Наименование атрибута")
    attr_field_type: Mapped[Optional[str]] = mapped_column(String(50), comment="Тип поля")
    requirement: Mapped[Optional[str]] = mapped_column(String(20), comment="Обязательность")
    is_multiplicable: Mapped[bool] = mapped_column(Boolean, default=False, comment="Мультиплицируемость")
    layer: Mapped[Optional[str]] = mapped_column(String(20), comment="Слой")
    preset_url: Mapped[Optional[str]] = mapped_column(Text, comment="URL пресета")
    etag: Mapped[Optional[str]] = mapped_column(String(100), comment="ETag")
    
    __table_args__ = (
        UniqueConstraint("cat_id", "attr_id", name="uq_cat_attr"),
        UniqueConstraint("tnved", "attr_id", name="uq_tnved_attr"),
    )
    
    def __repr__(self) -> str:
        return f"<AttributeModel(attr_id={self.attr_id}, name='{self.attr_name}')>"


# ============================================================================
# МОДЕЛЬ: SyncState - Курсоры синхронизации
# ============================================================================


class SyncState(Base):
    """
    Модель состояния синхронизации.
    
    Хранит курсоры: последние offset'ы, даты среза, ETag справочников.
    """
    
    __tablename__ = "sync_state"
    
    key: Mapped[str] = mapped_column(String(100), primary_key=True, comment="Ключ состояния")
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, comment="Значение (JSON)")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
    )
    
    def __repr__(self) -> str:
        return f"<SyncState(key='{self.key}', value={self.value})>"


# ============================================================================
# ФАБРИКА ДВИЖКА И СЕССИИ
# ============================================================================


def create_database_engine(database_url: str, echo: bool = False) -> "Engine":
    """
    Создаёт движок SQLAlchemy для подключения к БД.
    
    Args:
        database_url: URL подключения к базе данных
        echo: Если True, выводить SQL-запросы в лог
        
    Returns:
        Экземпляр SQLAlchemy Engine
    """
    engine = create_engine(
        database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,  # Проверка соединения перед использованием
    )
    
    # Обработка JSONB для SQLite (эмуляция через JSON)
    @event.listens_for(engine, "connect")
    def on_connect(dbapi_conn, connection_record):
        """Настраивает обработку JSON для SQLite."""
        pass  # SQLite автоматически обрабатывает JSON через тип JSON
    
    return engine


def get_session(engine: "Engine") -> Session:
    """
    Создаёт новую сессию SQLAlchemy.
    
    Args:
        engine: Экземпляр SQLAlchemy Engine
        
    Returns:
        Экземпляр Session
    """
    return Session(bind=engine, autoflush=False, autocommit=False)


# Для type hints
from sqlalchemy.engine import Engine
