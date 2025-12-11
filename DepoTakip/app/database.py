import os
from sqlmodel import SQLModel, Session, create_engine

# 1. VERİTABANI BAĞLANTISI
# ---------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./depo.db")

# Render PostgreSQL düzeltmesi (postgres:// -> postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Motoru (Engine) Oluştur
# SQLModel create_engine kullanıyoruz (SQLAlchemy wrapper)
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False} # SQLite için gerekli
    )
else:
    engine = create_engine(DATABASE_URL)


# 2. BAŞLANGIÇ AYARLARI (INIT)
# ---------------------------------------------------------
def init_db():
    """
    Uygulama başlarken tabloları oluşturur.
    SQLModel kullandığımız için 'Base.metadata' yerine 
    'SQLModel.metadata' kullanıyoruz.
    """
    # Modelleri import et ki SQLModel hafızasına alsın
    import app.models 
    
    # Tabloları veritabanında yarat
    SQLModel.metadata.create_all(engine)


# 3. SESSION YÖNETİMİ (DEPENDENCY)
# ---------------------------------------------------------
def get_session():
    """
    Her istekte yeni bir veritabanı oturumu açar ve kapatır.
    Router'larda 'db: Session = Depends(get_session)' olarak kullanılır.
    """
    # Klasik sessionmaker yerine SQLModel Session kullanıyoruz.
    # Böylece .exec(), .get() gibi modern komutlar çalışır.
    with Session(engine) as session:
        yield session