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

app = FastAPI(title="Depo V13 Final Fix")

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
        return None
    try:
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Hatası: {e}")
        return None

# --- GÜVENLİ BAŞLANGIÇ ---
@app.on_event("startup")
def startup():
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        
        # Tabloları teker teker, güvenli şekilde oluştur
        cur.execute("CREATE TABLE IF NOT EXISTS Kullanicilar (id SERIAL PRIMARY KEY, kadi VARCHAR(50) UNIQUE, sifre VARCHAR(100), rol VARCHAR(20));")
        cur.execute("CREATE TABLE IF NOT EXISTS Bolumler (id SERIAL PRIMARY KEY, ad VARCHAR(100) UNIQUE);")
        cur.execute("CREATE TABLE IF NOT EXISTS Personel (id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100));")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Urunler (
                id SERIAL PRIMARY KEY, 
                ad VARCHAR(200), 
                sku VARCHAR(50) UNIQUE, 
                birim VARCHAR(20), 
                tur VARCHAR(20), 
                guvenlik_stogu INTEGER DEFAULT 0
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Hareketler (
                id SERIAL PRIMARY KEY,
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                islem_tipi VARCHAR(20), 
                urun_id INTEGER REFERENCES Urunler(id) ON DELETE CASCADE,
                bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL,
                personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL,
                miktar REAL NOT NULL,
                aciklama TEXT
            );
        """)
        
        # Admin kullanıcısını ekle (Hata vermez, varsa geçer)
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'sky1911', 'admin') ON CONFLICT DO NOTHING")
        conn.commit()
        
    except Exception as e:
        print(f"Başlangıç Hatası: {e}")
    finally:
        conn.close()

# --- MODELLER ---
class LoginReq(BaseModel):
    kadi: str
    sifre: str

class GenelReq(BaseModel):
    ad: str

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

class RaporFiltre(BaseModel):
    baslangic: str
    bitis: str

# --- ARAYÜZ ---
@app.get("/", response_class=HTMLResponse)
def index():
    # BURAYI DÜZELTTİM: ARTIK TEK SATIR DEĞİL, HATASIZ
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Hata: index.html dosyası bulunamadı.</h1>"

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
    else:
        return []
        
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/ekle/bolum")
def add_bolum(r: GenelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Bolumler (ad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/ekle/personel")
def add_personel(r: GenelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/ekle/urun")
def add_urun(r: UrunReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Urunler (ad,sku,birim,tur,guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", 
                    (r.ad, r.sku, r.birim, r.tur, r.guvenlik))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/hareket")
def hareket(r: IslemReq):
    conn = get_db()
    cur = conn.cursor()
    
    # Stok kontrolü (Çıkışsa)
    if r.tip == 'CIKIS':
        cur.execute("SELECT COALESCE(SUM(CASE WHEN islem_tipi='GIRIS' THEN miktar ELSE -miktar END), 0) as stok FROM Hareketler WHERE urun_id=%s AND bolum_id=%s", (r.urun_id, r.bolum_id))
        stok = cur.fetchone()['stok']
        if stok < r.miktar:
            conn.close()
            raise HTTPException(status_code=400, detail="Yetersiz Stok")
    
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,%s,%s)", 
                (r.urun_id, r.bolum_id, r.tip, r.miktar, r.aciklama))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

@app.post("/api/zimmet")
def zimmet(r: ZimmetReq):
    conn = get_db()
    cur = conn.cursor()
    
    # Sadece Demirbaş kontrolü
    cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
    tur = cur.fetchone()['tur']
    if tur != 'Demirbaş':
        conn.close()
        raise HTTPException(status_code=400, detail="HATA: Sarf malzemesi zimmetlenemez! Lütfen 'Stok Çıkış' yapın.")
    
    cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,'ZIMMET',%s,'Zimmet')", 
                (r.urun_id, r.bolum_id, r.personel_id, r.miktar))
    conn.commit()
    conn.close()
    return {"msg": "ok"}

# --- RAPORLAMA ---
@app.post("/api/rapor/stok")
def rapor_stok(f: RaporFiltre):
    conn = get_db()
    cur = conn.cursor()
    
    # ANLIK STOK DURUMU
    sorgu = """
        SELECT b.ad as bolum, u.ad as urun, u.sku, u.tur, u.guvenlik_stogu,
        COALESCE(SUM(CASE WHEN h.islem_tipi='GIRIS' THEN h.miktar ELSE 0 END),0) - 
        COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','ZIMMET') THEN h.miktar ELSE 0 END),0) as mevcut
        FROM Urunler u 
        CROSS JOIN Bolumler b 
        LEFT JOIN Hareketler h ON u.id=h.urun_id AND b.id=h.bolum_id 
        GROUP BY u.id, b.id, u.ad, u.sku, u.tur, u.guvenlik_stogu
        HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi='GIRIS' THEN h.miktar ELSE 0 END),0) - 
                COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','ZIMMET') THEN h.miktar ELSE 0 END),0)) > 0
        ORDER BY u.ad
    """
    cur.execute(sorgu)
    stok = cur.fetchall()
    
    # HAREKET DÖKÜMÜ
    hareket_sorgu = """
        SELECT h.tarih, h.islem_tipi, b.ad as bolum, u.ad as urun, h.miktar, h.aciklama
        FROM Hareketler h
        JOIN Urunler u ON h.urun_id = u.id
        LEFT JOIN Bolumler b ON h.bolum_id = b.id
        WHERE h.tarih BETWEEN %s AND %s
        ORDER BY h.tarih DESC
    """
    t1 = f"{f.baslangic} 00:00:00"
    t2 = f"{f.bitis} 23:59:59"
    cur.execute(hareket_sorgu, (t1, t2))
    hareketler = cur.fetchall()
    
    conn.close()
    return {"stok": stok, "hareketler": hareketler}

@app.get("/api/rapor/zimmet")
def rapor_zimmet():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT p.ad_soyad, u.ad as urun, h.miktar, h.tarih FROM Hareketler h JOIN Personel p ON h.personel_id=p.id JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='ZIMMET' ORDER BY h.tarih DESC")
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/ai")
def ai(r: SoruReq):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok"}
    
    # Veri al (Bugünkü durum)
    tarih = datetime.now().strftime("%Y-%m-%d")
    # Mevcut fonksiyonu manuel çağırıyoruz
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT u.ad, SUM(h.miktar) as miktar FROM Hareketler h JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='GIRIS' GROUP BY u.ad")
    girisler = cur.fetchall()
    conn.close()
    
    txt = f"STOK RAPORU: {str(girisler)}\nSORU: {r.soru}"
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
                cur.execute("INSERT INTO Bolumler (ad) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (str(row['Bölüm']).strip(),))
                bid = cur.fetchone()
                if not bid:
                    cur.execute("SELECT id FROM Bolumler WHERE ad=%s", (str(row['Bölüm']).strip(),))
                    bid = cur.fetchone()
                
                cur.execute("INSERT INTO Urunler (ad,sku,birim,tur,guvenlik_stogu) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id", 
                            (str(row['Ürün Adı']), str(row['SKU']), row['Birim'], row['Tür'], int(row['Güvenlik Stoğu'])))
                uid = cur.fetchone()
                if not uid:
                    cur.execute("SELECT id FROM Urunler WHERE sku=%s", (str(row['SKU']),))
                    uid = cur.fetchone()
                
                cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'GIRIS',%s,'Excel')", 
                            (uid['id'], bid['id'], float(row['Miktar'])))
                c+=1
            except:
                pass
        conn.commit()
        conn.close()
        return {"msg": f"{c} kayıt eklendi"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))