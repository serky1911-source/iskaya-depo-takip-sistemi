import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. GÜVENLİK ADIMI:
# Adresi doğrudan buraya yazmıyoruz. Bilgisayardan veya Render'dan "DATABASE_URL" ismini istiyoruz.
# Eğer Render'da değilsek (lokal bilgisayarındaysan) hata vermesin diye otomatik SQLite seçiyoruz.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./depo.db")

# 2. RENDER UYUMLULUĞU:
# Render bazen adresi 'postgres://' olarak verir ama SQLAlchemy 'postgresql://' ister.
# Bu düzeltme hayat kurtarır:
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 3. BAĞLANTI MOTORU (Engine)
if "sqlite" in SQLALCHEMY_DATABASE_URL:
    # SQLite için özel ayar (check_same_thread)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL için standart ayar
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()