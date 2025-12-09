import os
import io
from datetime import datetime
from typing import List, Optional, Dict, Any

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai
import pandas as pd

load_dotenv()

app = FastAPI(title="Depo Yönetim Sistemi V10 Enterprise")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- KONFİGÜRASYON ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_db():
    """Veritabanı bağlantısı sağlar ve otomatik kapanır."""
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise HTTPException(status_code=500, detail="Veritabanı URL (DATABASE_URL) bulunamadı!")
    try:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Bağlantı Hatası: {e}")
        raise HTTPException(status_code=500, detail="Veritabanına erişilemiyor.")

# --- KURULUM ---
@app.on_event("startup")
def veritabani_baslat():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Tablo şeması - İlişkisel ve Tutarlı
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Kullanicilar (id SERIAL PRIMARY KEY, kadi VARCHAR(50) UNIQUE NOT NULL, sifre VARCHAR(100) NOT NULL, rol VARCHAR(20));
                CREATE TABLE IF NOT EXISTS Bolumler (id SERIAL PRIMARY KEY, ad VARCHAR(100) UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS Personel (id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100) NOT NULL);
                CREATE TABLE IF NOT EXISTS Urunler (
                    id SERIAL PRIMARY KEY, 
                    ad VARCHAR(200) NOT NULL, 
                    sku VARCHAR(50) UNIQUE NOT NULL, 
                    birim VARCHAR(20), 
                    tur VARCHAR(20), 
                    guvenlik_stogu INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS Hareketler (
                    id SERIAL PRIMARY KEY,
                    urun_id INTEGER REFERENCES Urunler(id) ON DELETE CASCADE,
                    bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL,
                    islem_tipi VARCHAR(20) NOT NULL, -- GIRIS, CIKIS, TRANSFER, ZIMMET
                    miktar REAL NOT NULL,
                    tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    aciklama TEXT,
                    personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL
                );
            """)
            
            # Varsayılan Kullanıcılar
            cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'admin123', 'admin') ON CONFLICT DO NOTHING")
            cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici') ON CONFLICT DO NOTHING")
            conn.commit()
            print("✅ Veritabanı ve Tablolar Hazır (V10.0)")
    except Exception as e:
        print(f"❌ Kurulum Hatası: {e}")
    finally:
        conn.close()

# --- DTO (Veri Transfer Objeleri) ---
class LoginReq(BaseModel): kadi: str; sifre: str
class BolumReq(BaseModel): ad: str
class PersonelReq(BaseModel): ad_soyad: str
class UrunReq(BaseModel): ad: str; sku: str; birim: str; tur: str; guvenlik_stogu: int
class IslemReq(BaseModel): sku: str; bolum_id: int; miktar: float; aciklama: str = ""
class TransferReq(BaseModel): sku: str; cikis_bolum_id: int; giris_bolum_id: int; miktar: float
class ZimmetReq(BaseModel): sku: str; personel_id: int; bolum_id: int; miktar: float
class SilmeReq(BaseModel): id: int
class SoruReq(BaseModel): soru: str

# --- WEB ARAYÜZÜ ---
@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f: return f.read()
    except: return "<h1>Sistem Bakımda: index.html yüklenemedi.</h1>"

# --- API ENDPOINTLERİ (RESTful Standart) ---

# 1. GİRİŞ
@app.post("/api/auth/login")
def login(req: LoginReq):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM Kullanicilar WHERE kadi=%s AND sifre=%s", (req.kadi, req.sifre))
        user = cur.fetchone()
    conn.close()
    if user: return {"status": "success", "rol": user['rol'], "ad": user['kadi']}
    raise HTTPException(401, "Kullanıcı adı veya şifre hatalı")

# 2. TANIMLAMALAR (GET, POST, DELETE)
@app.get("/api/data/{tip}")
def get_data(tip: str):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            if tip == "bolumler": cur.execute("SELECT * FROM Bolumler ORDER BY ad")
            elif tip == "personel": cur.execute("SELECT * FROM Personel ORDER BY ad_soyad")
            elif tip == "urunler": cur.execute("SELECT * FROM Urunler ORDER BY ad")
            else: raise HTTPException(400, "Geçersiz veri tipi")
            return cur.fetchall()
    finally: conn.close()

@app.post("/api/bolumler")
def add_bolum(req: BolumReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO Bolumler (ad) VALUES (%s)", (req.ad,))
            conn.commit()
        return {"msg": "Bölüm eklendi"}
    except psycopg2.IntegrityError: raise HTTPException(400, "Bu bölüm zaten var!")
    finally: conn.close()

@app.delete("/api/bolumler/{id}")
def del_bolum(id: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM Bolumler WHERE id=%s", (id,))
        conn.commit()
    conn.close()
    return {"msg": "Silindi"}

@app.post("/api/personel")
def add_personel(req: PersonelReq):
    conn = get_db(); cur=conn.cursor(); cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (req.ad_soyad,)); conn.commit(); conn.close(); return {"msg":"Ok"}

@app.delete("/api/personel/{id}")
def del_personel(id: int):
    conn = get_db(); cur=conn.cursor(); cur.execute("DELETE FROM Personel WHERE id=%s", (id,)); conn.commit(); conn.close(); return {"msg":"Ok"}

@app.post("/api/urunler")
def add_urun(req: UrunReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO Urunler (ad, sku, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", 
                        (req.ad, req.sku, req.birim, req.tur, req.guvenlik_stogu))
            conn.commit()
        return {"msg": "Ürün tanımlandı"}
    except psycopg2.IntegrityError: raise HTTPException(400, "Bu SKU kodu zaten kullanılıyor!")
    finally: conn.close()

@app.delete("/api/urunler/{id}")
def del_urun(id: int):
    conn = get_db(); cur=conn.cursor(); cur.execute("DELETE FROM Urunler WHERE id=%s", (id,)); conn.commit(); conn.close(); return {"msg":"Ok"}

# 3. İŞLEMLER (Stok Giriş/Çıkış)
@app.post("/api/islem/giris")
def stok_giris(req: IslemReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM Urunler WHERE sku=%s", (req.sku,))
            urun = cur.fetchone()
            if not urun: raise HTTPException(404, "Ürün bulunamadı! Önce tanımlayınız.")
            
            cur.execute("""
                INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
                VALUES (%s, %s, 'GIRIS', %s, %s)
            """, (urun['id'], req.bolum_id, req.miktar, req.aciklama))
            conn.commit()
        return {"msg": "Giriş Başarılı"}
    finally: conn.close()

@app.post("/api/islem/cikis")
def stok_cikis(req: IslemReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM Urunler WHERE sku=%s", (req.sku,))
            urun = cur.fetchone()
            if not urun: raise HTTPException(404, "Ürün bulunamadı")
            
            # Stok Kontrolü (Opsiyonel ama önerilir)
            # Şimdilik direkt çıkışa izin veriyoruz
            
            cur.execute("""
                INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
                VALUES (%s, %s, 'CIKIS', %s, %s)
            """, (urun['id'], req.bolum_id, req.miktar, req.aciklama))
            conn.commit()
        return {"msg": "Çıkış Başarılı"}
    finally: conn.close()

@app.post("/api/islem/transfer")
def stok_transfer(req: TransferReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM Urunler WHERE sku=%s", (req.sku,))
            urun = cur.fetchone()
            if not urun: raise HTTPException(404, "Ürün bulunamadı")
            
            # 1. Çıkış Yap
            cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'TRANSFER_CIKIS', %s, %s)",
                        (urun['id'], req.cikis_bolum_id, req.miktar, f"Transfer->{req.giris_bolum_id}"))
            # 2. Giriş Yap
            cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'TRANSFER_GIRIS', %s, %s)",
                        (urun['id'], req.giris_bolum_id, req.miktar, f"Transfer<-{req.cikis_bolum_id}"))
            conn.commit()
        return {"msg": "Transfer Başarılı"}
    finally: conn.close()

@app.post("/api/islem/zimmet")
def stok_zimmet(req: ZimmetReq):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM Urunler WHERE sku=%s", (req.sku,))
            urun = cur.fetchone()
            if not urun: raise HTTPException(404, "Ürün yok")
            
            # Depodan Çıkış (Zimmet olarak)
            cur.execute("""
                INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama, personel_id) 
                VALUES (%s, %s, 'ZIMMET', %s, 'Zimmet Verildi', %s)
            """, (urun['id'], req.bolum_id, req.miktar, req.personel_id))
            conn.commit()
        return {"msg": "Zimmetlendi"}
    finally: conn.close()

# 4. RAPORLAMA (Tek ve Güçlü SQL)
@app.get("/api/rapor/stok")
def rapor_stok():
    conn = get_db()
    with conn.cursor() as cur:
        # Tüm hareketleri toplayıp net stoğu bulan sihirli sorgu
        cur.execute("""
            SELECT 
                u.ad as urun_adi, 
                u.sku, 
                u.tur, 
                u.guvenlik_stogu,
                b.ad as bolum_adi,
                COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS', 'TRANSFER_GIRIS') THEN h.miktar ELSE 0 END), 0) -
                COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'TRANSFER_CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0) as mevcut
            FROM Urunler u
            CROSS JOIN Bolumler b
            LEFT JOIN Hareketler h ON u.id = h.urun_id AND b.id = h.bolum_id
            GROUP BY u.id, b.id, u.ad, u.sku, u.tur, u.guvenlik_stogu, b.ad
            HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS', 'TRANSFER_GIRIS') THEN h.miktar ELSE 0 END), 0) -
                    COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'TRANSFER_CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0)) <> 0
            ORDER BY u.ad
        """)
        data = cur.fetchall()
    conn.close()
    return data

@app.get("/api/rapor/zimmet")
def rapor_zimmet():
    conn = get_db(); cur=conn.cursor()
    cur.execute("""
        SELECT p.ad_soyad, u.ad as urun_adi, h.miktar, h.tarih 
        FROM Hareketler h
        JOIN Personel p ON h.personel_id = p.id
        JOIN Urunler u ON h.urun_id = u.id
        WHERE h.islem_tipi = 'ZIMMET'
        ORDER BY h.tarih DESC
    """)
    res = cur.fetchall(); conn.close(); return res

# 5. YAPAY ZEKA
@app.post("/api/ai/sor")
def ai_sor(req: SoruReq):
    if not GEMINI_API_KEY: return {"cevap": "API Anahtarı eksik"}
    
    stoklar = rapor_stok()
    zimmetler = rapor_zimmet()
    
    context = "STOKLAR:\n" + "\n".join([f"- {s['bolum_adi']}: {s['mevcut']} adet {s['urun_adi']}" for s in stoklar])
    context += "\n\nZİMMETLER:\n" + "\n".join([f"- {z['ad_soyad']}: {z['miktar']} adet {z['urun_adi']}" for z in zimmetler])
    
    try:
        # Model seçimi
        modeller = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        model = next((m for m in modeller if 'flash' in m), 'gemini-pro')
        
        ai = genai.GenerativeModel(model)
        cevap = ai.generate_content(f"Sen depo sorumlususun. Veri:\n{context}\nSoru: {req.soru}")
        return {"cevap": cevap.text}
    except Exception as e: return {"cevap": f"Hata oluştu: {str(e)}"}

# 6. EXCEL IMPORT
@app.post("/api/admin/excel")
async def excel_import(file: UploadFile = File(...)):
    try:
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content))
        conn = get_db()
        with conn.cursor() as cur:
            eklenen = 0
            for _, row in df.iterrows():
                try:
                    # Bölüm
                    b_ad = str(row['Bölüm']).strip()
                    cur.execute("INSERT INTO Bolumler (ad) VALUES (%s) ON CONFLICT (ad) DO NOTHING RETURNING id", (b_ad,))
                    bid = cur.fetchone()
                    if not bid: cur.execute("SELECT id FROM Bolumler WHERE ad=%s", (b_ad,)); bid = cur.fetchone()
                    
                    # Ürün
                    sku = str(row['SKU']).strip()
                    cur.execute("INSERT INTO Urunler (ad, sku, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (sku) DO NOTHING RETURNING id",
                                (str(row['Ürün Adı']), sku, row['Birim'], row['Tür'], int(row['Güvenlik Stoğu'])))
                    uid = cur.fetchone()
                    if not uid: cur.execute("SELECT id FROM Urunler WHERE sku=%s", (sku,)); uid = cur.fetchone()
                    
                    # Giriş Hareketi
                    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'GIRIS', %s, 'Excel Import')",
                                (uid['id'], bid['id'], float(row['Miktar'])))
                    eklenen += 1
                except Exception as e: print(f"Satır hatası: {e}")
            conn.commit()
        conn.close()
        return {"msg": f"{eklenen} kayıt işlendi."}
    except Exception as e: raise HTTPException(500, str(e))