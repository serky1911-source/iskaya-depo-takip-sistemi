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

app = FastAPI(title="Depo V12 Final Fix")

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
    if not url:
        print("UYARI: DATABASE_URL yok")
        return None
    try:
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Hatası: {e}")
        return None

# --- SİSTEMİ SIFIRLA VE YENİDEN KUR (RESET) ---
def sistem_reset():
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        
        # 1. ESKİ TABLOLARI SİL
        cur.execute("DROP TABLE IF EXISTS Hareketler CASCADE;")
        cur.execute("DROP TABLE IF EXISTS Urunler CASCADE;")
        cur.execute("DROP TABLE IF EXISTS Personel CASCADE;")
        cur.execute("DROP TABLE IF EXISTS Bolumler CASCADE;")
        cur.execute("DROP TABLE IF EXISTS Kullanicilar CASCADE;")
        
        # 2. YENİ TABLOLARI KUR
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
                birim VARCHAR(20),
                tur VARCHAR(20),
                guvenlik_stogu INTEGER DEFAULT 0
            );
        """)
        
        cur.execute("""
            CREATE TABLE Hareketler (
                id SERIAL PRIMARY KEY,
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                islem_tipi VARCHAR(20) NOT NULL,
                urun_id INTEGER REFERENCES Urunler(id) ON DELETE CASCADE,
                bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL,
                personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL,
                miktar REAL NOT NULL,
                aciklama TEXT
            );
        """)
        
        # 3. KULLANICIYI OLUŞTUR
        print("🛠️ Kullanıcılar oluşturuluyor...")
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'sky1911', 'admin')")
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici')")
        
        # 4. ÖRNEK VERİ
        cur.execute("INSERT INTO Bolumler (ad) VALUES ('Ana Depo')")
        
        conn.commit()
        print("✅ SİSTEM SIFIRLANDI. Giriş: admin / sky1911")
        
    except Exception as e:
        print(f"❌ Kurulum Hatası: {e}")
    finally:
        conn.close()

@app.on_event("startup")
def startup_event():
    sistem_reset()

# --- MODELLER ---
class LoginReq(BaseModel):
    kadi: str
    sifre: str

class BolumReq(BaseModel):
    ad: str

class PersonelReq(BaseModel):
    ad_soyad: str

class UrunReq(BaseModel):
    ad: str
    sku: str
    birim: str
    tur: str
    guvenlik: int

class IslemReq(BaseModel):
    tip: str
    urun_id: int
    bolum_id: int
    miktar: float
    aciklama: str = ""

class ZimmetReq(BaseModel):
    urun_id: int
    personel_id: int
    bolum_id: int
    miktar: float

class SoruReq(BaseModel):
    soru: str

@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>index.html yok</h1>"

# --- API ---
@app.post("/api/login")
def login(r: LoginReq):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM Kullanicilar WHERE kadi=%s AND sifre=%s", (r.kadi, r.sifre))
    user = cur.fetchone()
    conn.close()
    
    if user:
        return {"ok": True, "rol": user['rol'], "ad": user['kadi']}
    
    raise HTTPException(status_code=401, detail="Hatalı Giriş")

@app.get("/api/list/{tip}")
def get_list(tip: str):
    conn = get_db()
    cur = conn.cursor()
    
    if tip == 'bolum':
        cur.execute("SELECT * FROM Bolumler ORDER BY ad")
    elif tip == 'personel':
        cur.execute("SELECT * FROM Personel ORDER BY ad_soyad")
    elif tip == 'urun':
        cur.execute("SELECT * FROM Urunler ORDER BY ad")
    
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/ekle/bolum")
def add_bolum(r: BolumReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Bolumler (ad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "ok"}
    except:
        raise HTTPException(status_code=400, detail="Hata")
    finally:
        conn.close()

@app.post("/api/ekle/personel")
def add_personel(r: PersonelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (r.ad_soyad,))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/ekle/urun")
def add_urun(r: UrunReq):
    conn = get_db()
    cur = conn.cursor()
    try: 
        cur.execute("INSERT INTO Urunler (ad, sku, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", (r.ad, r.sku, r.birim, r.tur, r.guvenlik))
        conn.commit()
        return {"msg": "ok"}
    except:
        raise HTTPException(status_code=400, detail="SKU Çakışması")
    finally:
        conn.close()

@app.post("/api/hareket")
def hareket(r: IslemReq):
    conn = get_db()
    cur = conn.cursor()
    
    # Stok Kontrolü (Çıkışsa)
    if r.tip == 'CIKIS':
        cur.execute("SELECT COALESCE(SUM(CASE WHEN islem_tipi='GIRIS' THEN miktar ELSE -miktar END), 0) as stok FROM Hareketler WHERE urun_id=%s AND bolum_id=%s", (r.urun_id, r.bolum_id))
        mevcut = cur.fetchone()['stok']
        if mevcut < r.miktar:
            conn.close()
            raise HTTPException(status_code=400, detail="Yetersiz Stok")
    
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,%s,%s)", (r.urun_id, r.bolum_id, r.tip, r.miktar, r.aciklama))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.post("/api/zimmet")
def zimmet(r: ZimmetReq):
    conn = get_db()
    cur = conn.cursor()
    
    # Sadece Demirbaş
    cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
    tur = cur.fetchone()['tur']
    if tur != 'Demirbaş':
        conn.close()
        raise HTTPException(status_code=400, detail="Sadece Demirbaş zimmetlenir")
    
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,'ZIMMET',%s,'Zimmet')", (r.urun_id, r.bolum_id, r.personel_id, r.miktar))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.get("/api/rapor/stok")
def rapor_stok():
    conn = get_db()
    cur = conn.cursor()
    
    # Net Stok
    cur.execute("""
        SELECT b.ad as bolum, u.ad as urun, u.sku, u.tur, 
        COALESCE(SUM(CASE WHEN h.islem_tipi='GIRIS' THEN h.miktar ELSE 0 END),0) - 
        COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','ZIMMET') THEN h.miktar ELSE 0 END),0) as mevcut
        FROM Urunler u CROSS JOIN Bolumler b 
        LEFT JOIN Hareketler h ON u.id=h.urun_id AND b.id=h.bolum_id 
        GROUP BY u.id, b.id, u.ad, u.sku, u.tur 
        ORDER BY u.ad
    """)
    
    data = cur.fetchall()
    # Sadece stoku 0'dan büyük olanları filtrele
    stok = [x for x in data if x['mevcut'] > 0]
    
    conn.close()
    return stok

@app.get("/api/rapor/zimmet")
def rapor_zimmet_getir():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT p.ad_soyad, u.ad as urun, h.miktar, h.tarih FROM Hareketler h JOIN Personel p ON h.personel_id=p.id JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='ZIMMET'")
    zimmet = cur.fetchall()
    conn.close()
    return zimmet

@app.post("/api/ai")
def ai(r: SoruReq):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok"}
    
    stok = rapor_stok()
    # zimmet raporunu tekrar çağırıyoruz
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT p.ad_soyad, u.ad as urun, h.miktar FROM Hareketler h JOIN Personel p ON h.personel_id=p.id JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='ZIMMET'")
    zimmet_data = cur.fetchall()
    conn.close()

    txt = f"STOK: {stok}\nZIMMET: {zimmet_data}\nSORU: {r.soru}"
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        return {"cevap": model.generate_content(txt).text}
    except:
        return {"cevap": "AI Hatası"}

@app.post("/api/admin/excel")
async def excel(file: UploadFile = File(...)):
    try:
        df = pd.read_excel(io.BytesIO(await file.read()))
        conn = get_db()
        cur = conn.cursor()
        c = 0
        
        for _, row in df.iterrows():
            try:
                # Bölüm
                bolum_ad = str(row['Bölüm']).strip()
                cur.execute("INSERT INTO Bolumler (ad) VALUES (%s) ON CONFLICT (ad) DO NOTHING RETURNING id", (bolum_ad,))
                bid_res = cur.fetchone()
                
                if bid_res:
                    bid = bid_res['id']
                else:
                    cur.execute("SELECT id FROM Bolumler WHERE ad=%s", (bolum_ad,))
                    bid = cur.fetchone()['id']
                
                # Ürün
                urun_ad = str(row['Ürün Adı']).strip()
                sku = str(row['SKU']).strip()
                birim = row['Birim']
                tur = row['Tür']
                guv = int(row['Güvenlik Stoğu'])
                
                cur.execute("INSERT INTO Urunler (ad, sku, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (sku) DO NOTHING RETURNING id", (urun_ad, sku, birim, tur, guv))
                uid_res = cur.fetchone()
                
                if uid_res:
                    uid = uid_res['id']
                else:
                    cur.execute("SELECT id FROM Urunler WHERE sku=%s", (sku,))
                    uid = cur.fetchone()['id']
                
                # Giriş
                miktar = float(row['Miktar'])
                cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'GIRIS',%s,'Excel')", (uid, bid, miktar))
                c += 1
            except Exception as e:
                print(f"Excel Satır Hatası: {e}")
                pass
                
        conn.commit()
        conn.close()
        return {"msg": f"{c} kayıt eklendi"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))