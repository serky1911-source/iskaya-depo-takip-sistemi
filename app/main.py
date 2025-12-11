from fastapi.responses import HTMLResponse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import init_db

# --- ROUTERLARI IMPORT ET ---
from app.routers import tanimlamalar, islemler, demirbas, rapor

# --- YAŞAM DÖNGÜSÜ (LIFESPAN) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Depo V17 Başlatılıyor...")
    init_db()  # Tabloları oluşturur/günceller
    yield
    print("🛑 Depo V17 Kapatılıyor...")

# --- UYGULAMA AYARLARI ---
app = FastAPI(
    title="Depo Yönetim Sistemi V17",
    description="Kusursuz Stok ve Zimmet Takip Sistemi",
    version="17.1.0",
    lifespan=lifespan
)

# --- GÜVENLİK (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ROUTERLARI SİSTEME BAĞLAMA ---
app.include_router(tanimlamalar.router)
app.include_router(islemler.router)
app.include_router(demirbas.router)
app.include_router(rapor.router)

# --- ARAYÜZ (HTML) ---
@app.get("/", response_class=HTMLResponse)
def ana_sayfa():
    # index.html dosyasını okuyup ekrana basar.
    # Not: index.html dosyasının main.py'nin bir üst klasöründe (ana dizinde) olması gerekir.
    # Eğer Render hata verirse yolu "../index.html" yapmayı deneyebilirim şimdilik böyle bırakıyorum.
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()