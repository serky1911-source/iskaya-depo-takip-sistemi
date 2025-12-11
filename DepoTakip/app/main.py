from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

# DİKKAT: Başına nokta (.) koyduk. Bu "yanımdaki dosyalara bak" demektir.
# Böylece "app.database" hatası almayız.
from .database import engine, init_db
from .routers import demirbas, islemler, rapor, tanimlamalar

# Veritabanı tablolarını oluştur
init_db()

app = FastAPI()

# Router'ları (Sayfaları) sisteme dahil et
app.include_router(demirbas.router)
app.include_router(islemler.router)
app.include_router(rapor.router)
app.include_router(tanimlamalar.router)

# Statik dosyalar (HTML, CSS) için ayar
# index.html dosyanın ana dizinde (DepoTakip içinde) olduğunu varsayıyoruz.
if os.path.exists("index.html"):
    app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
async def root():
    # Eğer index.html varsa onu döndür, yoksa mesaj ver
    if os.path.exists("index.html"):
        from fastapi.responses import FileResponse
        return FileResponse("index.html")
    return {"message": "Depo Takip Sistemi Çalışıyor! (index.html bulunamadı)"}