import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Veritabanı URL Ayarı (Güvenli)
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

# 3. Oturum Oluşturucu
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# --- EKSİK OLAN 1: Başlangıçta Tabloları Kurar ---
def init_db():
    import app.models 
    Base.metadata.create_all(bind=engine)

# --- EKSİK OLAN 2: Router'ların Veritabanı Almasını Sağlar ---
def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()