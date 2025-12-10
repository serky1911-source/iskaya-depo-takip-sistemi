import os
import io
from datetime import datetime, timedelta
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import pandas as pd

# Yapay Zeka Kütüphanesi (Opsiyonel olduğu için try-except içine aldık)
try:
    import google.generativeai as genai
except ImportError:
    genai = None

load_dotenv()

app = FastAPI(title="Depo V16 Final Fix")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Yapay Zeka Ayarı
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY and genai:
    genai.configure(api_key=GEMINI_API_KEY)

# --- VERİTABANI BAĞLANTISI ---
def get_db():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print("UYARI: DATABASE_URL bulunamadı")
        return None
    try:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Bağlantı Hatası: {e}")
        return None

# --- SİSTEM KURULUMU ---
@app.on_event("startup")
def startup():
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        
        # Tabloları oluştur
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Kullanicilar (
                id SERIAL PRIMARY KEY, 
                kadi VARCHAR(50) UNIQUE, 
                sifre VARCHAR(100), 
                rol VARCHAR(20)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Bolumler (
                id SERIAL PRIMARY KEY, 
                ad VARCHAR(100) UNIQUE
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Personel (
                id SERIAL PRIMARY KEY, 
                ad_soyad VARCHAR(100)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Urunler (
                id SERIAL PRIMARY KEY, 
                ad VARCHAR(200) NOT NULL, 
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
                hedef_bolum_id INTEGER REFERENCES Bolumler(id) ON DELETE SET NULL, 
                personel_id INTEGER REFERENCES Personel(id) ON DELETE SET NULL, 
                miktar REAL NOT NULL,
                urun_durumu VARCHAR(20) DEFAULT 'SAGLAM',
                aciklama TEXT
            );
        """)
        
        # Admin Kullanıcısını Ekle/Güncelle
        cur.execute("INSERT INTO Kullanicilar (kadi, sifre, rol) VALUES ('admin', 'sky1911', 'admin') ON CONFLICT (kadi) DO UPDATE SET sifre = 'sky1911'")
        
        # Varsayılan Bölüm Ekle
        cur.execute("INSERT INTO Bolumler (ad) VALUES ('Ana Depo') ON CONFLICT DO NOTHING")
        
        conn.commit()
        print("✅ Sistem ve Veritabanı Hazır.")
        
    except Exception as e:
        print(f"Başlangıç Hatası: {e}")
    finally:
        if conn:
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

class GirisReq(BaseModel):
    urun_id: int
    depo_id: int
    miktar: float
    aciklama: str = ""

class TransferReq(BaseModel):
    urun_id: int
    cikis_depo_id: int
    giris_depo_id: int
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

class RaporFiltre(BaseModel):
    baslangic: str
    bitis: str

class SoruReq(BaseModel):
    soru: str

# --- ARAYÜZ (BU KISIM HATALIYDI, DÜZELTİLDİ) ---
@app.get("/", response_class=HTMLResponse)
def index():
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
    try:
        cur.execute("SELECT * FROM Kullanicilar WHERE kadi=%s AND sifre=%s", (r.kadi, r.sifre))
        user = cur.fetchone()
        if user:
            return {"ok": True, "rol": user['rol'], "ad": user['kadi']}
        else:
            raise HTTPException(status_code=401, detail="Hatalı Giriş")
    finally:
        conn.close()

@app.get("/api/list/{tip}")
def get_list(tip: str):
    conn = get_db()
    cur = conn.cursor()
    try:
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
        
        return cur.fetchall()
    finally:
        conn.close()

# EKLEME İŞLEMLERİ
@app.post("/api/ekle/bolum")
def add_b(r: GenelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Bolumler (ad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "Bölüm Eklendi"}
    except:
        raise HTTPException(status_code=400, detail="Bu bölüm zaten var")
    finally:
        conn.close()

@app.post("/api/ekle/personel")
def add_p(r: GenelReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (r.ad,))
        conn.commit()
        return {"msg": "Personel Eklendi"}
    finally:
        conn.close()

@app.post("/api/ekle/urun")
def add_u(r: UrunReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Urunler (ad,sku,birim,tur,guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", 
                    (r.ad, r.sku, r.birim, r.tur, r.guvenlik))
        conn.commit()
        return {"msg": "Ürün Eklendi"}
    except:
        raise HTTPException(status_code=400, detail="Bu SKU kodu zaten var")
    finally:
        conn.close()

# 1. MAL KABUL (GİRİŞ)
@app.post("/api/islem/giris")
def islem_giris(r: GirisReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'GIRIS',%s,%s)", 
                    (r.urun_id, r.depo_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Mal Kabul Yapıldı"}
    finally:
        conn.close()

# 2. SARF TRANSFER (DEPO -> DEPO)
@app.post("/api/islem/transfer")
def islem_transfer(r: TransferReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        # Tür Kontrolü
        cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
        res = cur.fetchone()
        if res and res['tur'] != 'SARF':
            raise HTTPException(status_code=400, detail="Demirbaş transferi yapılamaz! Zimmetleyiniz.")

        # Stok Kontrolü (Kaynak Depo)
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi IN ('GIRIS','TR_GIRIS','ZIMMET_IADE') THEN miktar ELSE -miktar END), 0) as stok 
            FROM Hareketler WHERE urun_id=%s AND bolum_id=%s
        """, (r.urun_id, r.cikis_depo_id))
        stok_res = cur.fetchone()
        stok = stok_res['stok'] if stok_res else 0
        
        if stok < r.miktar:
            raise HTTPException(status_code=400, detail=f"Kaynak depoda yetersiz stok! Mevcut: {stok}")

        # İşlem: Kaynaktan Çık, Hedefe Gir
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'TR_CIKIS',%s,%s)", 
                    (r.urun_id, r.cikis_depo_id, r.miktar, f"Transfer->HedefID:{r.giris_depo_id} " + r.aciklama))
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'TR_GIRIS',%s,%s)", 
                    (r.urun_id, r.giris_depo_id, r.miktar, f"Transfer<-KaynakID:{r.cikis_depo_id} " + r.aciklama))
        
        conn.commit()
        return {"msg": "Transfer Tamamlandı"}
    finally:
        conn.close()

# 3. SARF ÇIKIŞI (TÜKETİM)
@app.post("/api/islem/cikis")
def islem_cikis(r: CikisReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
        res = cur.fetchone()
        if res and res['tur'] != 'SARF':
            raise HTTPException(status_code=400, detail="Demirbaş 'Çıkış' yapılamaz, Zimmetlenmelidir!")
        
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi IN ('GIRIS','TR_GIRIS','ZIMMET_IADE') THEN miktar ELSE -miktar END), 0) as stok 
            FROM Hareketler WHERE urun_id=%s AND bolum_id=%s
        """, (r.urun_id, r.bolum_id))
        
        stok_res = cur.fetchone()
        stok = stok_res['stok'] if stok_res else 0
        
        if stok < r.miktar:
            raise HTTPException(status_code=400, detail=f"Yetersiz Stok! Mevcut: {stok}")
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'CIKIS',%s,%s)", 
                    (r.urun_id, r.bolum_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Çıkış Yapıldı"}
    finally:
        conn.close()

# 4. ZİMMET VERME
@app.post("/api/islem/zimmet_ver")
def islem_zimmet_ver(r: ZimmetReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT tur FROM Urunler WHERE id=%s", (r.urun_id,))
        res = cur.fetchone()
        if res and res['tur'] != 'DEMIRBAS':
            raise HTTPException(status_code=400, detail="Sarf malzemesi zimmetlenemez!")
        
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi IN ('GIRIS','TR_GIRIS','ZIMMET_IADE') THEN miktar ELSE -miktar END), 0) as stok 
            FROM Hareketler WHERE urun_id=%s AND bolum_id=%s
        """, (r.urun_id, r.bolum_id))
        
        stok_res = cur.fetchone()
        stok = stok_res['stok'] if stok_res else 0
        
        if stok < r.miktar:
            raise HTTPException(status_code=400, detail="Depoda bu demirbaş yok!")
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,%s,'ZIMMET_VER',%s,%s)", 
                    (r.urun_id, r.bolum_id, r.personel_id, r.miktar, r.aciklama))
        conn.commit()
        return {"msg": "Zimmetlendi"}
    finally:
        conn.close()

# 5. ZİMMET İADE
@app.post("/api/islem/zimmet_al")
def islem_zimmet_al(r: IadeReq):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN islem_tipi='ZIMMET_VER' THEN miktar ELSE -miktar END), 0) as zimmet 
            FROM Hareketler WHERE urun_id=%s AND personel_id=%s
        """, (r.urun_id, r.personel_id))
        
        zimmet_res = cur.fetchone()
        zimmet = zimmet_res['zimmet'] if zimmet_res else 0
        
        if zimmet < r.miktar:
            raise HTTPException(status_code=400, detail="Personelde bu kadar zimmet yok!")
        
        cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, personel_id, islem_tipi, miktar, urun_durumu, aciklama) VALUES (%s,%s,%s,'ZIMMET_IADE',%s,%s,%s)", 
                    (r.urun_id, r.depo_id, r.personel_id, r.miktar, r.durum, r.aciklama))
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
        # 1. ANLIK STOK (Tarihten Bağımsız - Şu an ne var?)
        cur.execute("""
            SELECT b.ad as bolum, u.ad as urun, u.sku, u.tur, u.guvenlik_stogu,
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS','TR_GIRIS','ZIMMET_IADE') THEN h.miktar ELSE 0 END),0) - 
            COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','TR_CIKIS','ZIMMET_VER') THEN h.miktar ELSE 0 END),0) as mevcut
            FROM Urunler u 
            CROSS JOIN Bolumler b 
            LEFT JOIN Hareketler h ON u.id=h.urun_id AND b.id=h.bolum_id 
            GROUP BY u.id, b.id, u.ad, u.sku, u.tur, u.guvenlik_stogu
            HAVING (COALESCE(SUM(CASE WHEN h.islem_tipi IN ('GIRIS','TR_GIRIS','ZIMMET_IADE') THEN h.miktar ELSE 0 END),0) - 
                    COALESCE(SUM(CASE WHEN h.islem_tipi IN ('CIKIS','TR_CIKIS','ZIMMET_VER') THEN h.miktar ELSE 0 END),0)) > 0
            ORDER BY u.ad
        """)
        stok = cur.fetchall()
        
        # 2. HAREKET DÖKÜMÜ (Tarih Filtreli)
        t1 = f"{f.baslangic} 00:00:00"
        t2 = f"{f.bitis} 23:59:59"
        cur.execute("""
            SELECT h.tarih, h.islem_tipi, b.ad as bolum, u.ad as urun, h.miktar, p.ad_soyad, h.aciklama
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
def rapor_zimmet_list():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.ad_soyad, u.ad as urun, 
            SUM(CASE WHEN h.islem_tipi='ZIMMET_VER' THEN h.miktar ELSE -h.miktar END) as miktar
            FROM Hareketler h 
            JOIN Personel p ON h.personel_id=p.id 
            JOIN Urunler u ON h.urun_id=u.id 
            WHERE h.islem_tipi IN ('ZIMMET_VER','ZIMMET_IADE')
            GROUP BY p.ad_soyad, u.ad
            HAVING SUM(CASE WHEN h.islem_tipi='ZIMMET_VER' THEN h.miktar ELSE -h.miktar END) > 0
        """)
        return cur.fetchall()
    finally:
        conn.close()

# AI
@app.post("/api/ai")
def ai(r: SoruReq):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok"}
    
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT u.ad, SUM(h.miktar) as toplam FROM Hareketler h JOIN Urunler u ON h.urun_id=u.id WHERE h.islem_tipi='GIRIS' GROUP BY u.ad")
        data = cur.fetchall()
        txt = f"STOK ÖZETİ: {data}\nSORU: {r.soru}"
        
        if genai:
            model = genai.GenerativeModel('gemini-1.5-flash')
            resp = model.generate_content(txt)
            return {"cevap": resp.text}
        else:
            return {"cevap": "AI Kütüphanesi Yüklü Değil"}
    except Exception as e:
        return {"cevap": f"Hata: {str(e)}"}
    finally:
        conn.close()

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
                birim = str(row['Birim'])
                tur = str(row['Tür'])
                guv = int(row['Güvenlik Stoğu'])
                
                cur.execute("""
                    INSERT INTO Urunler (ad,sku,birim,tur,guvenlik_stogu) 
                    VALUES (%s,%s,%s,%s,%s) 
                    ON CONFLICT (sku) DO NOTHING RETURNING id
                """, (urun_ad, sku, birim, tur, guv))
                
                uid_res = cur.fetchone()
                if uid_res:
                    uid = uid_res['id']
                else:
                    cur.execute("SELECT id FROM Urunler WHERE sku=%s", (sku,))
                    uid = cur.fetchone()['id']
                
                # Giriş
                miktar = float(row['Miktar'])
                cur.execute("INSERT INTO Hareketler (urun_id, bolum_id, islem_tipi, miktar, aciklama) VALUES (%s,%s,'GIRIS',%s,'Excel')", 
                            (uid, bid, miktar))
                c += 1
            except:
                pass
        
        conn.commit()
        return {"msg": f"{c} kayıt eklendi"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()