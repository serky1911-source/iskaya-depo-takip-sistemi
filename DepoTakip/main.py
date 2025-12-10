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

app = FastAPI(title="Depo V16 Final")

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

# --- BAŞLANGIÇ AYARLARI (VERİLERİ KORUR) ---
@app.on_event("startup")
def startup():
    conn = get_db()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # 1. TABLOLAR (YOKSA OLUŞTUR)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Kullanicilar (
                id SERIAL PRIMARY KEY,
                kadi VARCHAR(50) UNIQUE NOT NULL,
                sifre VARCHAR(100) NOT NULL,
                rol VARCHAR(20) NOT NULL
            );
        """)
        
        cur.execute("CREATE TABLE IF NOT EXISTS Bolumler (id SERIAL PRIMARY KEY, ad VARCHAR(100) UNIQUE NOT NULL);")
        cur.execute("CREATE TABLE IF NOT EXISTS Personel (id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100) NOT NULL);")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Urunler (
                id SERIAL PRIMARY KEY,
                ad VARCHAR(200) NOT NULL,
                ozellik VARCHAR(200),
                sku VARCHAR(50) UNIQUE NOT NULL,
                birim VARCHAR(20) NOT NULL,
                tur VARCHAR(20) NOT NULL,
                guvenlik_stogu INTEGER DEFAULT 0
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Hareketler (
                id SERIAL PRIMARY KEY,
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                islem_tipi VARCHAR(20) NOT NULL,
                urun_id INTEGER REFERENCES Urunler(id) ON DELETE CASCADE,
                bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL,
                personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL,
                miktar REAL NOT NULL,
                urun_durumu VARCHAR(20) DEFAULT 'SAGLAM',
                aciklama TEXT
            );
        """)
        
        # 2. SABİT KULLANICILAR (ZORLA GÜNCELLE)
        # Admin
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'sky1911', 'admin') ON CONFLICT (kadi) DO UPDATE SET sifre = 'sky1911', rol = 'admin'")
        
        # Veysel
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('veysel', 'veysel211', 'admin') ON CONFLICT (kadi) DO UPDATE SET sifre = 'veysel211', rol = 'admin'")
        
        # Mtekin
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('mtekin', 'tekinm', 'izleyici') ON CONFLICT (kadi) DO UPDATE SET sifre = 'tekinm', rol = 'izleyici'")
        
        # Varsayılan Depolar (Sadece yoksa ekle)
        cur.execute("INSERT INTO Bolumler (ad) VALUES ('Ana Depo') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO Bolumler (ad) VALUES ('Hurda Deposu') ON CONFLICT DO NOTHING")
        
        conn.commit()
        print("✅ Sistem Hazır. Kullanıcılar Güncellendi.")
        
    except Exception as e:
        print(f"Hata: {e}")
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
    ozellik: str
    sku: str
    birim: str
    tur: str
    guvenlik: int

class GirisReq(BaseModel):
    urun_id: int
    depo_id: int
    miktar: float
    aciklama: str = ""

class CikisReq(BaseModel):
    urun_id: int
    bolum_id: int
    miktar: float
    aciklama: str = ""

class ZimmetReq(BaseModel):
    urun_id: int
    personel_id: int
    bolum_id: int
    miktar: float
    aciklama: str = ""

class IadeReq(BaseModel):
    urun_id: int
    personel_id: int
    depo_id: int
    miktar: float
    durum: str
    aciklama: str = ""

class SoruReq(BaseModel):
    soru: str

class RaporFiltre(BaseModel):
    baslangic: str
    bitis: str

# --- ARAYÜZ ---
@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>Sistem Yükleniyor...</h1>"

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
    elif tip == 'urun_sarf':
        cur.execute("SELECT * FROM Urunler WHERE tur='SARF' ORDER BY ad")
    elif tip == 'urun_demirbas':
        cur.execute("SELECT * FROM Urunler WHERE tur='DEMIRBAS' ORDER BY ad")
    else:
        return []
        
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/ekle/bolum")
def add_b(r: GenelReq):
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
def add_p(r: GenelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "ok"}
    finally:
        conn.close()

@app.post("/api/ekle/urun")
def add_u(r: UrunReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Urunler (ad,ozellik,sku,birim,tur,guvenlik_stogu) VALUES (%s,%s,%s,%s,%s,%s)", 
                    (r.ad, r.ozellik, r.sku, r.birim, r.tur, r.guvenlik))
        conn.commit()
        return {"msg": "ok"}
    except:
        raise HTTPException(status_code=400, detail="SKU Mevcut")
    finally:
        conn.close()

# 1. STOK GİRİŞİ
@app.post("/api/islem/giris")
def islem_giris(r: GirisReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'GIRIS',%s,%s)", 
                    (r.urun_id, r.depo_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Giriş Yapıldı"}
    finally:
        conn.close()

# 2. SARF ÇIKIŞI
@app.post("/api/islem/cikis")
def islem_cikis(r: CikisReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        # Kontrol
        cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
        tur = cur.fetchone()
        if tur and tur['tur'] != 'SARF':
            raise HTTPException(status_code=400, detail="Demirbaşlar 'Çıkış' yapılamaz, Zimmetlenmelidir!")
        
        # Stok
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi IN ('GIRIS','ZIMMET_AL') THEN miktar ELSE -miktar END), 0) as stok 
            FROM Hareketler WHERE urun_id=%s AND bolum_id=%s
        """, (r.urun_id, r.bolum_id))
        stok = cur.fetchone()['stok']
        
        if stok < r.miktar:
            raise HTTPException(status_code=400, detail=f"Yetersiz Stok! Mevcut: {stok}")
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'CIKIS',%s,%s)", 
                    (r.urun_id, r.bolum_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Çıkış Yapıldı"}
    finally:
        conn.close()

# 3. ZİMMET VERME
@app.post("/api/islem/zimmet_ver")
def islem_zimmet_ver(r: ZimmetReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        # Kontrol
        cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
        tur = cur.fetchone()
        if tur and tur['tur'] != 'DEMIRBAS':
            raise HTTPException(status_code=400, detail="Sarf malzemesi zimmetlenemez!")
        
        # Stok
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi IN ('GIRIS','ZIMMET_AL') THEN miktar ELSE -miktar END), 0) as stok 
            FROM Hareketler WHERE urun_id=%s AND bolum_id=%s
        """, (r.urun_id, r.bolum_id))
        stok = cur.fetchone()['stok']
        
        if stok < r.miktar:
            raise HTTPException(status_code=400, detail="Depoda bu kadar demirbaş yok!")
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,'ZIMMET_VER',%s,%s)", 
                    (r.urun_id, r.bolum_id, r.personel_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Zimmetlendi"}
    finally:
        conn.close()

# 4. ZİMMET İADE
@app.post("/api/islem/zimmet_al")
def islem_zimmet_al(r: IadeReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi='ZIMMET_VER' THEN miktar ELSE -miktar END), 0) as zimmet 
            FROM Hareketler WHERE urun_id=%s AND personel_id=%s
        """, (r.urun_id, r.personel_id))
        mevcut = cur.fetchone()['zimmet']
        
        if mevcut < r.miktar:
            raise HTTPException(status_code=400, detail="Personelde bu kadar zimmet yok!")
        
        cur.execute("""
            INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, urun_durumu, aciklama) 
            VALUES (%s,%s,%s,'ZIMMET_AL',%s,%s,%s)
        """, (r.urun_id, r.depo_id, r.personel_id, r.miktar, r.durum, r.aciklama))
        conn.commit()
        return {"msg": "İade Alındı"}
    finally:
        conn.close()

# --- RAPORLAR ---
@app.post("/api/rapor/stok")
def rapor_stok(f: RaporFiltre):
    conn = get_db()
    cur = conn.cursor()
    try:
        # MEVCUT STOK
        cur.execute("""
            SELECT b.ad as bolum, u.ad as urun, u.ozellik, u.birim, u.tur,
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS','ZIMMET_AL') THEN h.miktar ELSE 0 END),0) - 
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','ZIMMET_VER') THEN h.miktar ELSE 0 END),0) as mevcut
            FROM Urunler u 
            CROSS JOIN Bolumler b 
            LEFT JOIN Hareketler h ON u.id=h.urun_id AND b.id=h.bolum_id 
            GROUP BY u.id, b.id, u.ad, u.ozellik, u.birim, u.tur
            HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS','ZIMMET_AL') THEN h.miktar ELSE 0 END),0) - 
                    COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','ZIMMET_VER') THEN h.miktar ELSE 0 END),0)) > 0
            ORDER BY u.ad
        """)
        stok = cur.fetchall()
        
        # HAREKETLER
        t1 = f"{f.baslangic} 00:00:00"
        t2 = f"{f.bitis} 23:59:59"
        cur.execute("""
            SELECT h.tarih, h.islem_tipi, b.ad as bolum, u.ad as urun, h.miktar, p.ad_soyad, h.urun_durumu, h.aciklama
            FROM Hareketler h
            JOIN Urunler u ON h.urun_id = u.id
            LEFT JOIN Bolumler b ON h.bolum_id = b.id
            LEFT JOIN Personel p ON h.personel_id = p.id
            WHERE h.tarih BETWEEN %s AND %s
            ORDER BY h.tarih DESC
        """, (t1, t2))
        hareketler = cur.fetchall()
        
        return {"stok": stok, "hareketler": hareketler}
    finally:
        conn.close()

@app.get("/api/rapor/zimmet")
def rapor_zimmet_getir():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.ad_soyad, u.ad as urun, u.ozellik,
            SUM(CASE WHEN h.islem_tipi='ZIMMET_VER' THEN h.miktar ELSE -h.miktar END) as miktar
            FROM Hareketler h 
            JOIN Personel p ON h.personel_id=p.id 
            JOIN Urunler u ON h.urun_id=u.id 
            WHERE h.islem_tipi IN ('ZIMMET_VER','ZIMMET_AL')
            GROUP BY p.ad_soyad, u.ad, u.ozellik
            HAVING SUM(CASE WHEN h.islem_tipi='ZIMMET_VER' THEN h.miktar ELSE -h.miktar END) > 0
        """)
        return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/ai")
def ai(r: SoruReq):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok"}
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT u.ad, SUM(h.miktar) FROM Hareketler h JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='GIRIS' GROUP BY u.ad")
    ozet = cur.fetchall()
    conn.close()
    
    txt = f"STOK ÖZETİ: {ozet}\nSORU: {r.soru}"
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
                cur.execute("INSERT INTO Bolumler (ad) VALUES (%s) ON CONFLICT (ad) DO NOTHING RETURNING id", (str(row['Bölüm']).strip(),))
                bid_row = cur.fetchone()
                if bid_row:
                    bid = bid_row['id']
                else:
                    cur.execute("SELECT id FROM Bolumler WHERE ad=%s", (str(row['Bölüm']).strip(),))
                    bid = cur.fetchone()['id']
                
                # Ürün
                cur.execute("""
                    INSERT INTO Urunler (ad, ozellik, sku, birim, tur, guvenlik_stogu) 
                    VALUES (%s, '', %s, %s, %s, %s) 
                    ON CONFLICT (sku) DO NOTHING RETURNING id
                """, (str(row['Ürün Adı']), str(row['SKU']), str(row['Birim']), str(row['Tür']), int(row['Güvenlik Stoğu'])))
                uid_row = cur.fetchone()
                if uid_row:
                    uid = uid_row['id']
                else:
                    cur.execute("SELECT id FROM Urunler WHERE sku=%s", (str(row['SKU']),))
                    uid = cur.fetchone()['id']
                
                # Giriş Hareketi
                cur.execute("""
                    INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) 
                    VALUES (%s, %s, 'GIRIS', %s, 'Excel Import')
                """, (uid, bid, float(row['Miktar'])))
                c += 1
            except:
                pass
        
        conn.commit()
        return {"msg": f"{c} kayıt eklendi"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()