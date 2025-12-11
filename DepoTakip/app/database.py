import os
from sqlmodel import SQLModel, Session, create_engine

# 1. VERİTABANI BAĞLANTISI
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./depo.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

# 2. BAŞLANGIÇ AYARLARI (INIT)
def init_db():
    import app.models 
    
    # --- DİKKAT: BU SATIR VERİTABANINI SIFIRLAR ---
    # Tabloları önce siliyoruz ki en güncel haliyle (aktif_mi vb.) tekrar kurulsun.
    SQLModel.metadata.drop_all(engine) 
    # ----------------------------------------------

    SQLModel.metadata.create_all(engine)

# 3. SESSION YÖNETİMİ
def get_session():
    with Session(engine) as session:
        yield session