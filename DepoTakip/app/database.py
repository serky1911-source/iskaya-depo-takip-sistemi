import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Veritabanı URL Ayarı
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./depo.db")

# Render uyumluluğu (postgres:// -> postgresql://)
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 2. Engine (Motor) Oluşturma
if "sqlite" in SQLALCHEMY_DATABASE_URL:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# --- İŞTE EKSİK OLAN FONKSİYON ---
def init_db():
    # Modellerin (tabloların) veritabanına işlenmesi için bu komut şart
    # Models importunu fonksiyon içine alıyoruz ki döngüsel import hatası olmasın
    import app.models 
    Base.metadata.create_all(bind=engine)