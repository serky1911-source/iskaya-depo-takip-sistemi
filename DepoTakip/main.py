import os
import io
from datetime import datetime
from typing import List, Optional, Dict, Any

# Kütüphaneler
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai
import pandas as pd

# Ortam değişkenlerini yükle
load_dotenv()

# Uygulama Başlatma
app = FastAPI(title="Depo Yönetim Sistemi Final")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- KONFIGÜRASYON ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- VERİTABANI BAĞLANTISI ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("UYARI: DATABASE_URL bulunamadı.")
        return None
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Bağlantı Hatası: {e}")
        return None

# --- VERİTABANI KURULUMU ---
def veritabani_kur():
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # Tablolar
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Kullanicilar (
                kullanici_id SERIAL PRIMARY KEY, 
                kullanici_adi VARCHAR(50) UNIQUE, 
                sifre VARCHAR(100), 
                rol VARCHAR(20)
            );
        """)
        
        cur.execute("CREATE TABLE IF NOT EXISTS Bolumler (bolum_id SERIAL PRIMARY KEY, bolum_adi VARCHAR(100) UNIQUE);")
        cur.execute("CREATE TABLE IF NOT EXISTS Personel (personel_id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100));")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Urunler (
                urun_id SERIAL PRIMARY KEY, 
                urun_adi VARCHAR(200), 
                SKU VARCHAR(50) UNIQUE, 
                birim VARCHAR(20), 
                tur VARCHAR(20), 
                guvenlik_stogu INTEGER DEFAULT 0
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Hareketler (
                hareket_id SERIAL PRIMARY KEY, 
                urun_id INTEGER REFERENCES Urunler(urun_id), 
                bolum_id INTEGER REFERENCES Bolumler(bolum_id), 
                islem_tipi VARCHAR(20), 
                miktar REAL, 
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                aciklama TEXT, 
                personel_id INTEGER REFERENCES Personel(personel_id)
            );
        """)
        
        # --- KRİTİK DÜZELTME: ŞİFREYİ ZORLA GÜNCELLE ---
        # Önce kullanıcı yoksa ekle
        cur.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('admin', 'sky1911', 'admin') ON CONFLICT (kullanici_adi) DO NOTHING")
        cur.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici') ON CONFLICT (kullanici_adi) DO NOTHING")
        
        # ŞİMDİ DE ŞİFREYİ KESİN OLARAK 'sky1911' YAP
        cur.execute("UPDATE Kullanicilar SET sifre = 'sky1911' WHERE kullanici_adi = 'admin'")
        
        conn.commit()
        print("✅ Veritabanı hazır. Admin şifresi: sky1911 olarak ayarlandı.")
        
    except Exception as e:
        print(f"❌ Kurulum Hatası: {e}")
    finally:
        conn.close()

@app.on_event("startup")
def startup_event():
    veritabani_kur()

# --- MODELLER ---
class LoginReq(BaseModel):
    kullanici_adi: str
    sifre: str

class BolumReq(BaseModel):
    bolum_adi: str

class PersonelReq(BaseModel):
    ad_soyad: str

class UrunReq(BaseModel):
    urun_adi: str
    sku: str
    birim: str
    tur: str
    guvenlik_stogu: int = 0

class IslemReq(BaseModel):
    sku: str
    bolum_id: int
    miktar: float
    aciklama: str = ""

class TransferReq(BaseModel):
    sku: str
    cikis_bolum_id: int
    giris_bolum_id: int
    miktar: float

class ZimmetReq(BaseModel):
    sku: str
    personel_id: int
    bolum_id: int
    miktar: float

class SoruReq(BaseModel):
    soru: str

# --- ARAYÜZ ---
@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Hata: index.html dosyası bulunamadı!</h1>"

# --- API ---

@app.post("/api/auth/login")
def login(req: LoginReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (req.kullanici_adi, req.sifre))
        user = cur.fetchone()
        if user:
            return {"status": "ok", "rol": user['rol'], "ad": user['kullanici_adi']}
        else:
            raise HTTPException(status_code=401, detail="Hatalı Giriş")
    finally:
        conn.close()

@app.get("/api/data/{tip}")
def get_data(tip: str):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if tip == "bolumler":
            cur.execute("SELECT * FROM Bolumler ORDER BY bolum_adi")
        elif tip == "personel":
            cur.execute("SELECT * FROM Personel ORDER BY ad_soyad")
        elif tip == "urunler":
            cur.execute("SELECT * FROM Urunler ORDER BY urun_adi")
        else:
            return []
        return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/bolumler")
def add_bolum(req: BolumReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Bolumler (bolum_adi) VALUES (%s)", (req.bolum_adi,))
        conn.commit()
        return {"msg": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/api/personel")
def add_personel(req: PersonelReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (req.ad_soyad,))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/urunler")
def add_urun(req: UrunReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO Urunler (urun_adi, SKU, birim, tur, guvenlik_stogu) 
            VALUES (%s, %s, %s, %s, %s)
        """, (req.urun_adi, req.sku, req.birim, req.tur, req.guvenlik_stogu))
        conn.commit()
        return {"msg": "ok"}
    except Exception:
        raise HTTPException(status_code=400, detail="Bu SKU kodu zaten var.")
    finally:
        conn.close()

@app.delete("/api/bolumler/{id}")
def del_bolum(id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Bolumler WHERE bolum_id=%s", (id,))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.delete("/api/personel/{id}")
def del_personel(id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Personel WHERE personel_id=%s", (id,))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.delete("/api/urunler/{id}")
def del_urun(id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Urunler WHERE urun_id=%s", (id,))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.post("/api/islem/giris")
def giris_yap(req: IslemReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (req.sku,))
        u = cur.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="Ürün Yok")
        
        cur.execute("""
            INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
            VALUES (%s, %s, 'GIRIS', %s, %s)
        """, (u['urun_id'], req.bolum_id, req.miktar, req.aciklama))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/islem/cikis")
def cikis_yap(req: IslemReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (req.sku,))
        u = cur.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="Ürün Yok")
        
        cur.execute("""
            INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
            VALUES (%s, %s, 'CIKIS', %s, %s)
        """, (u['urun_id'], req.bolum_id, req.miktar, req.aciklama))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/islem/transfer")
def transfer_yap(req: TransferReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (req.sku,))
        u = cur.fetchone()
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'TR_CIKIS', %s, %s)", 
                    (u['urun_id'], req.cikis_bolum_id, req.miktar, f"Transfer->{req.giris_bolum_id}"))
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s, %s, 'TR_GIRIS', %s, %s)", 
                    (u['urun_id'], req.giris_bolum_id, req.miktar, f"Transfer<-{req.cikis_bolum_id}"))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/islem/zimmet")
def zimmet_yap(req: ZimmetReq):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (req.sku,))
        u = cur.fetchone()
        
        cur.execute("""
            INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama, personel_id) 
            VALUES (%s, %s, 'ZIMMET', %s, 'Zimmet', %s)
        """, (u['urun_id'], req.bolum_id, req.miktar, req.personel_id))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.get("/api/rapor/stok")
def rapor_stok():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
        SELECT 
            u.urun_adi, 
            u.SKU, 
            u.tur, 
            u.guvenlik_stogu,
            b.bolum_adi,
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS', 'TR_GIRIS') THEN h.miktar ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'TR_CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0) as mevcut
        FROM Urunler u
        CROSS JOIN Bolumler b
        LEFT JOIN Hareketler h ON u.urun_id = h.urun_id AND b.bolum_id = h.bolum_id
        GROUP BY u.urun_id, b.bolum_id, u.urun_adi, u.SKU, u.tur, u.guvenlik_stogu, b.bolum_adi
        HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS', 'TR_GIRIS') THEN h.miktar ELSE 0 END), 0) -
                COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS', 'TR_CIKIS', 'ZIMMET') THEN h.miktar ELSE 0 END), 0)) <> 0
        ORDER BY u.urun_adi
        """
        cur.execute(query)
        return cur.fetchall()
    finally:
        conn.close()

@app.get("/api/rapor/zimmet")
def rapor_zimmet():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.ad_soyad, u.urun_adi, h.miktar, h.tarih 
            FROM Hareketler h 
            JOIN Personel p ON h.personel_id = p.personel_id 
            JOIN Urunler u ON h.urun_id = u.urun_id 
            WHERE h.islem_tipi = 'ZIMMET' 
            ORDER BY h.tarih DESC
        """)
        return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/ai/sor")
def ai(req: SoruReq):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok"}
    
    stok = rapor_stok()
    zimmet = rapor_zimmet()
    
    txt = "STOK:\n"
    for s in stok:
        txt += f"{s['bolum_adi']}: {s['mevcut']} {s['urun_adi']}\n"
        
    txt += "\nZIMMET:\n"
    for z in zimmet:
        txt += f"{z['ad_soyad']}: {z['miktar']} {z['urun_adi']}\n"
    
    try:
        modeller = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        model_adi = next((m for m in modeller if 'flash' in m), 'models/gemini-pro')
        
        ai_model = genai.GenerativeModel(model_adi)
        response = ai_model.generate_content(f"Sen depo asistanısın. Veri:\n{txt}\nSoru:{req.soru}")
        return {"cevap": response.text}
    except Exception as e:
        return {"cevap": f"Hata: {e}"}

@app.post("/api/admin/excel")
async def excel(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        conn = get_db_connection()
        cur = conn.cursor()
        count = 0
        
        for _, row in df.iterrows():
            try:
                # Bölüm
                bolum_adi = str(row['Bölüm']).strip()
                cur.execute("INSERT INTO Bolumler (bolum_adi) VALUES (%s) ON CONFLICT (bolum_adi) DO NOTHING RETURNING bolum_id", (bolum_adi,))
                bid = cur.fetchone()
                if not bid:
                    cur.execute("SELECT bolum_id FROM Bolumler WHERE bolum_adi=%s", (bolum_adi,))
                    bid = cur.fetchone()
                
                # Ürün
                u_ad = str(row['Ürün Adı']).strip()
                sku = str(row['SKU']).strip()
                cur.execute("""
                    INSERT INTO Urunler (urun_adi, SKU, birim, tur, guvenlik_stogu) 
                    VALUES (%s, %s, %s, %s, %s) 
                    ON CONFLICT (SKU) DO NOTHING RETURNING urun_id
                """, (u_ad, sku, row['Birim'], row['Tür'], int(row['Güvenlik Stoğu'])))
                uid = cur.fetchone()
                if not uid:
                    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (sku,))
                    uid = cur.fetchone()
                
                # Hareket
                cur.execute("""
                    INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
                    VALUES (%s, %s, 'GIRIS', %s, 'Excel')
                """, (uid['urun_id'], bid['bolum_id'], float(row['Miktar'])))
                count += 1
            except Exception as e:
                print(f"Satır hatası: {e}")
                
        conn.commit()
        conn.close()
        return {"msg": f"{count} kayıt eklendi"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))