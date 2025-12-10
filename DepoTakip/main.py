import os
import io
from datetime import datetime
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai
import pandas as pd

load_dotenv()

app = FastAPI(title="Depo Pro V11 - Enterprise")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_db():
    url = os.environ.get('DATABASE_URL')
    if not url: raise HTTPException(500, "Veritabanı URL'si (DATABASE_URL) bulunamadı.")
    try: return psycopg2.connect(url, cursor_factory=RealDictCursor)
    except Exception as e: print(f"DB Hatası: {e}"); raise HTTPException(500, "Veritabanı Bağlantı Hatası")

# --- SİSTEMİ SIFIRLA VE YENİDEN KUR ---
def sistem_kurulumu():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Önce temizlik (Bozuk tabloları kaldır)
            # NOT: Bu işlem veri kaybına yol açar ama temiz başlangıç için şarttır.
            cur.execute("DROP TABLE IF EXISTS Hareketler CASCADE;")
            cur.execute("DROP TABLE IF EXISTS Stoklar CASCADE;") # Eski kalıntılar varsa
            cur.execute("DROP TABLE IF EXISTS Urunler CASCADE;")
            cur.execute("DROP TABLE IF EXISTS Personel CASCADE;")
            cur.execute("DROP TABLE IF EXISTS Bolumler CASCADE;")
            cur.execute("DROP TABLE IF EXISTS Kullanicilar CASCADE;")
            
            # Yeni Tablolar (Profesyonel Şema)
            cur.execute("""
                CREATE TABLE Kullanicilar (
                    id SERIAL PRIMARY KEY, 
                    kadi VARCHAR(50) UNIQUE NOT NULL, 
                    sifre VARCHAR(100) NOT NULL, 
                    rol VARCHAR(20) NOT NULL
                );
            """)
            cur.execute("CREATE TABLE Bolumler (id SERIAL PRIMARY KEY, ad VARCHAR(100) UNIQUE NOT NULL);")
            cur.execute("CREATE TABLE Personel (id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100) NOT NULL);")
            cur.execute("""
                CREATE TABLE Urunler (
                    id SERIAL PRIMARY KEY, 
                    ad VARCHAR(200) NOT NULL, 
                    sku VARCHAR(50) UNIQUE NOT NULL, 
                    birim VARCHAR(20) NOT NULL, 
                    tur VARCHAR(20) NOT NULL, -- 'Sarf' veya 'Demirbaş'
                    guvenlik_stogu INTEGER DEFAULT 0
                );
            """)
            cur.execute("""
                CREATE TABLE Hareketler (
                    id SERIAL PRIMARY KEY,
                    tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    islem_tipi VARCHAR(20) NOT NULL, -- GIRIS, CIKIS, ZIMMET, TRANSFER
                    urun_id INTEGER REFERENCES Urunler(id) ON DELETE CASCADE,
                    bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL, -- Hangi depoda olduğu
                    personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL, -- Kime zimmetlendiği
                    miktar REAL NOT NULL,
                    aciklama TEXT
                );
            """)
            
            # Varsayılan Veriler
            cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'admin123', 'admin')")
            cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici')")
            
            # Örnek Depo
            cur.execute("INSERT INTO Bolumler (ad) VALUES ('Ana Depo')")
            
            conn.commit()
            print("✅ SİSTEM BAŞARIYLA SIFIRLANDI VE KURULDU (V11)")
    except Exception as e:
        print(f"❌ Kurulum Hatası: {e}")
    finally:
        conn.close()

# Render her başladığında değil, sadece tablo yoksa çalışsın diye kontrol ekleyebiliriz 
# ama "her şeyi baştan yap" dediğin için bu seferlik her başlatmada sıfırlayacak kodu açıyorum.
# DİKKAT: Veriler her restartta silinir. Bunu engellemek için alttaki satırı yorum satırı yapmalısın.
@app.on_event("startup")
def startup():
    # Güvenlik için: Sadece tablolar yoksa kur.
    # Eğer "Reset" istiyorsan veritabanını Render panelinden silebilirsin.
    # Şimdilik "Soft Check" yapıyoruz.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.Kullanicilar')")
    if not cur.fetchone()[0]:
        sistem_kurulumu()
    conn.close()

# --- MODELLER ---
class LoginReq(BaseModel): kadi: str; sifre: str
class EkleReq(BaseModel): ad: str
class UrunReq(BaseModel): ad: str; sku: str; birim: str; tur: str; guvenlik: int
class IslemReq(BaseModel): tip: str; urun_id: int; bolum_id: int; miktar: float; aciklama: str = ""
class TransferReq(BaseModel): urun_id: int; cikis_id: int; giris_id: int; miktar: float
class ZimmetReq(BaseModel): urun_id: int; personel_id: int; bolum_id: int; miktar: float
class SoruReq(BaseModel): soru: str

# --- WEB UI ---
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    try: with open("index.html", "r", encoding="utf-8") as f: return f.read()
    except: return "<h1>Sistem Yükleniyor... Lütfen bekleyin.</h1>"

# --- API ---

@app.post("/api/login")
def login(r: LoginReq):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM Kullanicilar WHERE kadi=%s AND sifre=%s", (r.kadi, r.sifre))
    user = cur.fetchone(); conn.close()
    if user: return {"ok": True, "rol": user['rol'], "ad": user['kadi']}
    raise HTTPException(401, "Hatalı şifre")

# LİSTELEME
@app.get("/api/list/{tip}")
def liste_getir(tip: str):
    conn = get_db(); cur = conn.cursor()
    if tip == 'bolum': cur.execute("SELECT * FROM Bolumler ORDER BY ad")
    elif tip == 'personel': cur.execute("SELECT * FROM Personel ORDER BY ad_soyad")
    elif tip == 'urun': cur.execute("SELECT * FROM Urunler ORDER BY ad")
    res = cur.fetchall(); conn.close()
    return res

# EKLEME
@app.post("/api/ekle/bolum")
def ekle_bolum(r: EkleReq):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO Bolumler (ad) VALUES (%s)", (r.ad,)); conn.commit(); conn.close()
    return {"msg": "Bölüm Eklendi"}

@app.post("/api/ekle/personel")
def ekle_personel(r: EkleReq):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (r.ad,)); conn.commit(); conn.close()
    return {"msg": "Personel Eklendi"}

@app.post("/api/ekle/urun")
def ekle_urun(r: UrunReq):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Urunler (ad, sku, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", 
                    (r.ad, r.sku, r.birim, r.tur, r.guvenlik))
        conn.commit(); conn.close()
        return {"msg": "Ürün Eklendi"}
    except: raise HTTPException(400, "Bu SKU zaten kayıtlı!")

# HAREKETLER (GİRİŞ / ÇIKIŞ)
@app.post("/api/hareket")
def hareket_yap(r: IslemReq):
    conn = get_db(); cur = conn.cursor()
    # Stok Kontrolü (Çıkış için)
    if r.tip == 'CIKIS':
        cur.execute("SELECT COALESCE(SUM(CASE WHEN islem_tipi='GIRIS' THEN miktar ELSE -miktar END), 0) as stok FROM Hareketler WHERE urun_id=%s AND bolum_id=%s", (r.urun_id, r.bolum_id))
        stok = cur.fetchone()['stok']
        if stok < r.miktar: raise HTTPException(400, f"Yetersiz Stok! Mevcut: {stok}")

    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, %s, %s, %s)",
                (r.urun_id, r.bolum_id, r.tip, r.miktar, r.aciklama))
    conn.commit(); conn.close()
    return {"msg": "İşlem Başarılı"}

# TRANSFER
@app.post("/api/transfer")
def transfer_yap(r: TransferReq):
    conn = get_db(); cur = conn.cursor()
    # Stok Var mı?
    cur.execute("SELECT COALESCE(SUM(CASE WHEN islem_tipi='GIRIS' THEN miktar ELSE -miktar END), 0) as stok FROM Hareketler WHERE urun_id=%s AND bolum_id=%s", (r.urun_id, r.cikis_id))
    stok = cur.fetchone()['stok']
    if stok < r.miktar: raise HTTPException(400, f"Kaynak depoda stok yetersiz! Mevcut: {stok}")

    # Çıkış ve Giriş Hareketleri
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'CIKIS', %s, 'Transfer Çıkış')", (r.urun_id, r.cikis_id, r.miktar))
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'GIRIS', %s, 'Transfer Giriş')", (r.urun_id, r.giris_id, r.miktar))
    conn.commit(); conn.close()
    return {"msg": "Transfer Yapıldı"}

# ZİMMET
@app.post("/api/zimmet")
def zimmet_yap(r: ZimmetReq):
    conn = get_db(); cur = conn.cursor()
    # Sadece Demirbaş zimmetlenebilir mi? Kontrol edelim.
    cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
    tur = cur.fetchone()['tur']
    if tur != 'Demirbaş': raise HTTPException(400, "Sadece 'Demirbaş' ürünler zimmetlenebilir. Sarf malzeme 'Çıkış' yapılmalıdır.")

    # Stok Kontrol
    cur.execute("SELECT COALESCE(SUM(CASE WHEN islem_tipi='GIRIS' THEN miktar ELSE -miktar END), 0) as stok FROM Hareketler WHERE urun_id=%s AND bolum_id=%s", (r.urun_id, r.bolum_id))
    stok = cur.fetchone()['stok']
    if stok < r.miktar: raise HTTPException(400, f"Stok yetersiz! Mevcut: {stok}")

    # Hareket Kaydı (Depodan Düş, Kişiye Ata)
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, %s, 'ZIMMET', %s, 'Zimmetlendi')",
                (r.urun_id, r.bolum_id, r.personel_id, r.miktar))
    conn.commit(); conn.close()
    return {"msg": "Zimmet Başarılı"}

# RAPORLAR
@app.get("/api/rapor/stok")
def rapor_stok():
    conn = get_db(); cur = conn.cursor()
    # Net Stok Hesabı: (Girişler) - (Çıkışlar + Zimmetler)
    # Not: Zimmet depodan düşer. Transfer zaten Giriş/Çıkış olarak işlendi.
    sorgu = """
    SELECT 
        b.ad as bolum,
        u.ad as urun,
        u.sku,
        u.tur,
        u.guvenlik_stogu,
        COALESCE(SUM(CASE WHEN h.islem_tipi = 'GIRIS' THEN h.miktar ELSE 0 END), 0) -
        COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0) as mevcut
    FROM Urunler u
    CROSS JOIN Bolumler b
    LEFT JOIN Hareketler h ON u.id = h.urun_id AND b.id = h.bolum_id
    GROUP BY u.id, b.id, u.ad, u.sku, u.tur, u.guvenlik_stogu, b.ad
    HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi = 'GIRIS' THEN h.miktar ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0)) > 0
    ORDER BY u.ad
    """
    cur.execute(sorgu); res = cur.fetchall(); conn.close()
    return res

@app.get("/api/rapor/zimmet")
def rapor_zimmet():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.ad_soyad, u.ad as urun, h.miktar, h.tarih 
        FROM Hareketler h
        JOIN Personel p ON h.personel_id = p.id
        JOIN Urunler u ON h.urun_id = u.id
        WHERE h.islem_tipi = 'ZIMMET'
        ORDER BY h.tarih DESC
    """)
    res = cur.fetchall(); conn.close(); return res

# AI
@app.post("/api/ai")
def sor_ai(r: SoruReq):
    if not GEMINI_API_KEY: return {"cevap": "API Key yok."}
    stok = rapor_stok(); zimmet = rapor_zimmet()
    txt = f"STOK: {stok}\nZİMMET: {zimmet}\nSORU: {r.soru}"
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        return {"cevap": model.generate_content(txt).text}
    except:
        model = genai.GenerativeModel('gemini-pro')
        return {"cevap": model.generate_content(txt).text}