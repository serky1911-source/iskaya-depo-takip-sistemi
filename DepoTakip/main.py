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

app = FastAPI()

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

def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise HTTPException(status_code=500, detail="Database URL yok")
    try:
        return psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    except Exception as e:
        print("DB Hatası:", e)
        raise HTTPException(status_code=500, detail="DB Bağlantı Hatası")

def veritabani_kur():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        sqls = [
            "CREATE TABLE IF NOT EXISTS Kullanicilar (kullanici_id SERIAL PRIMARY KEY, kullanici_adi VARCHAR(50) UNIQUE, sifre VARCHAR(100), rol VARCHAR(20));",
            "CREATE TABLE IF NOT EXISTS Bolumler (bolum_id SERIAL PRIMARY KEY, bolum_adi VARCHAR(100) UNIQUE);",
            "CREATE TABLE IF NOT EXISTS Personel (personel_id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100));",
            "CREATE TABLE IF NOT EXISTS Urunler (urun_id SERIAL PRIMARY KEY, urun_adi VARCHAR(200), SKU VARCHAR(50) UNIQUE, birim VARCHAR(20), tur VARCHAR(20), guvenlik_stogu INTEGER DEFAULT 0);",
            "CREATE TABLE IF NOT EXISTS StokGiris (giris_id SERIAL PRIMARY KEY, urun_id INTEGER, bolum_id INTEGER, miktar REAL, giris_tarihi TIMESTAMP, belge_no VARCHAR(100));",
            "CREATE TABLE IF NOT EXISTS StokCikis (cikis_id SERIAL PRIMARY KEY, urun_id INTEGER, bolum_id INTEGER, miktar REAL, cikis_tarihi TIMESTAMP, sevkiyat_no VARCHAR(100), aciklama TEXT);",
            "CREATE TABLE IF NOT EXISTS Zimmetler (zimmet_id SERIAL PRIMARY KEY, urun_id INTEGER, personel_id INTEGER, miktar REAL, verilis_tarihi TIMESTAMP);"
        ]
        
        for s in sqls:
            cur.execute(s)
        
        # Varsayılan Kullanıcılar
        cur.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('admin', 'admin123', 'admin') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici') ON CONFLICT DO NOTHING")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print("Kurulum hatası:", e)

@app.on_event("startup")
def startup_event():
    veritabani_kur()

# --- Modeller (Genişletilmiş Yazım - Hata Çıkarmaz) ---
class LoginModel(BaseModel):
    kullanici_adi: str
    sifre: str

class BolumModel(BaseModel):
    bolum_adi: str

class PersonelModel(BaseModel):
    ad_soyad: str

class UrunModel(BaseModel):
    urun_adi: str
    sku: str
    birim: str
    tur: str
    guvenlik_stogu: int = 0

class IslemModel(BaseModel):
    sku: str
    bolum_id: int
    miktar: float
    belge_no: str = ""

class TransferModel(BaseModel):
    sku: str
    cikis_bolum_id: int
    giris_bolum_id: int
    miktar: float

class ZimmetModel(BaseModel):
    sku: str
    personel_id: int
    bolum_id: int
    miktar: float = 1

class SoruModel(BaseModel):
    soru: str

class RaporFiltreModel(BaseModel):
    baslangic_tarihi: str
    bitis_tarihi: str
    bolum_id: int = 0

@app.get("/", response_class=HTMLResponse)
def ana_sayfa():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>index.html bulunamadı</h1>"

# --- RAPORLAMA ---
@app.get("/rapor/genel-stok")
def genel_stok():
    conn = get_db_connection()
    cur = conn.cursor()
    
    sorgu = """
    SELECT 
        u.urun_adi, 
        u.SKU, 
        u.tur, 
        u.guvenlik_stogu,
        b.bolum_adi,
        COALESCE(SUM(g.miktar), 0) as toplam_giris,
        COALESCE((
            SELECT SUM(c.miktar) 
            FROM StokCikis c 
            WHERE c.urun_id = u.urun_id AND c.bolum_id = b.bolum_id
        ), 0) as toplam_cikis
    FROM Urunler u
    JOIN StokGiris g ON u.urun_id = g.urun_id
    JOIN Bolumler b ON g.bolum_id = b.bolum_id
    GROUP BY u.urun_id, b.bolum_id, u.urun_adi, u.SKU, u.tur, u.guvenlik_stogu, b.bolum_adi
    ORDER BY u.urun_adi
    """
    cur.execute(sorgu)
    ham_veri = cur.fetchall()
    conn.close()
    
    sonuc = []
    for r in ham_veri:
        mevcut = r['toplam_giris'] - r['toplam_cikis']
        # Stok sıfır olsa bile listede görünsün ki "Ürün kayboldu" sanılmasın.
        # İsteğe bağlı olarak "if mevcut != 0:" diyerek gizleyebilirsin ama şimdilik kalsın.
        sonuc.append({
            "urun_adi": r['urun_adi'],
            "sku": r['sku'],
            "tur": r['tur'],
            "bolum": r['bolum_adi'],
            "mevcut": mevcut,
            "uyari": (r['tur'] == 'Sarf' and mevcut <= r['guvenlik_stogu'])
        })
    return sonuc

@app.get("/rapor/zimmetler")
def zimmet_listesi():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.ad_soyad, u.urun_adi, u.SKU, z.miktar, z.verilis_tarihi 
        FROM Zimmetler z 
        JOIN Personel p ON z.personel_id = p.personel_id 
        JOIN Urunler u ON z.urun_id = u.urun_id
        ORDER BY z.verilis_tarihi DESC
    """)
    res = cur.fetchall()
    conn.close()
    return res

# --- AI ---
@app.post("/ai/sor")
def ai_sor(m: SoruModel):
    if not GEMINI_API_KEY:
        return {"cevap": "API Key yok."}
    
    # Veriyi çek
    stoklar = genel_stok() 
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT p.ad_soyad, u.urun_adi, z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id")
    zimmetler = cur.fetchall()
    conn.close()
    
    context = "DEPO STOKLARI:\n" + "\n".join([f"- {s['bolum']} deposunda {s['mevcut']} adet {s['urun_adi']}." for s in stoklar])
    context += "\n\nZİMMETLER:\n" + "\n".join([f"- {z['ad_soyad']}: {z['miktar']} adet {z['urun_adi']}." for z in zimmetler])
    
    try:
        modeller = [md.name for md in genai.list_models() if 'generateContent' in md.supported_generation_methods]
        # Otomatik model seçimi (Flash öncelikli)
        model_name = next((x for x in modeller if 'flash' in x), modeller[0])
        
        ai = genai.GenerativeModel(model_name)
        response = ai.generate_content(f"Sen depo asistanısın. Veri:\n{context}\nSoru: {m.soru}")
        return {"cevap": response.text}
    except Exception as e:
        return {"cevap": f"Hata: {e}"}

# --- İŞLEMLER ---
@app.post("/stok/giris")
def stok_giris(d: IslemModel):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (d.sku,))
    urun = cur.fetchone()
    if not urun:
        raise HTTPException(404, "Ürün bulunamadı! Önce tanımlayın.")
    
    cur.execute("INSERT INTO StokGiris (urun_id, bolum_id, miktar, giris_tarihi, belge_no) VALUES (%s,%s,%s,%s,%s)", 
                (urun['urun_id'], d.bolum_id, d.miktar, datetime.now(), d.belge_no))
    conn.commit()
    conn.close()
    return {"msg": "Giriş yapıldı"}

@app.post("/stok/cikis")
def stok_cikis(d: IslemModel):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (d.sku,))
    urun = cur.fetchone()
    if not urun:
        raise HTTPException(404, "Ürün yok.")
    
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, sevkiyat_no, aciklama) VALUES (%s,%s,%s,%s,%s,%s)", 
                (urun['urun_id'], d.bolum_id, d.miktar, datetime.now(), d.belge_no, "Tüketim"))
    conn.commit()
    conn.close()
    return {"msg": "Çıkış yapıldı"}

@app.post("/stok/transfer")
def transfer(d: TransferModel):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (d.sku,))
    urun = cur.fetchone()
    now = datetime.now()
    
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, aciklama) VALUES (%s,%s,%s,%s,%s)", 
                (urun['urun_id'], d.cikis_bolum_id, d.miktar, now, f"Transfer->{d.giris_bolum_id}"))
    
    cur.execute("INSERT INTO StokGiris (urun_id, bolum_id, miktar, giris_tarihi, belge_no) VALUES (%s,%s,%s,%s,%s)", 
                (urun['urun_id'], d.giris_bolum_id, d.miktar, now, f"Transfer<-{d.cikis_bolum_id}"))
    conn.commit()
    conn.close()
    return {"msg": "Transfer tamam"}

@app.post("/zimmet/ver")
def zimmet_ver(d: ZimmetModel):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (d.sku,))
    urun = cur.fetchone()
    now = datetime.now()
    
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, aciklama) VALUES (%s,%s,%s,%s,%s)", 
                (urun['urun_id'], d.bolum_id, d.miktar, now, f"Zimmet->{d.personel_id}"))
    
    cur.execute("INSERT INTO Zimmetler (urun_id, personel_id, miktar, verilis_tarihi) VALUES (%s,%s,%s,%s)", 
                (urun['urun_id'], d.personel_id, d.miktar, now))
    conn.commit()
    conn.close()
    return {"msg": "Zimmetlendi"}

# --- EXCEL ---
@app.post("/admin/excel-yukle")
async def excel_yukle(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        conn = get_db_connection()
        cur = conn.cursor()
        
        for _, row in df.iterrows():
            # Bölüm
            cur.execute("SELECT bolum_id FROM Bolumler WHERE bolum_adi=%s", (str(row['Bölüm']).strip(),))
            b = cur.fetchone()
            if not b:
                cur.execute("INSERT INTO Bolumler (bolum_adi) VALUES (%s) RETURNING bolum_id", (str(row['Bölüm']).strip(),))
                bid = cur.fetchone()['bolum_id']
            else:
                bid = b['bolum_id']
            
            # Ürün
            cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (str(row['SKU']).strip(),))
            u = cur.fetchone()
            if not u: 
                cur.execute("INSERT INTO Urunler (urun_adi,SKU,birim,tur,guvenlik_stogu) VALUES (%s,%s,%s,%s,%s) RETURNING urun_id",
                            (str(row['Ürün Adı']).strip(), str(row['SKU']).strip(), str(row['Birim']), str(row['Tür']), int(row['Güvenlik Stoğu'])))
                uid = cur.fetchone()['urun_id']
            else:
                uid = u['urun_id']
            
            # Stok Ekle
            cur.execute("INSERT INTO StokGiris (urun_id,bolum_id,miktar,giris_tarihi,belge_no) VALUES (%s,%s,%s,%s,%s)",
                        (uid, bid, float(row['Miktar']), datetime.now(), "Excel"))
        
        conn.commit()
        conn.close()
        return {"mesaj": "Excel yüklendi"}
    except Exception as e:
        raise HTTPException(500, str(e))

# --- LİSTELER ---
@app.get("/bolumler/listele")
def bl():
    c = get_db_connection()
    x = c.cursor()
    x.execute("SELECT * FROM Bolumler ORDER BY bolum_adi")
    r = x.fetchall()
    c.close()
    return r

@app.get("/personel/listele")
def pl():
    c = get_db_connection()
    x = c.cursor()
    x.execute("SELECT * FROM Personel ORDER BY ad_soyad")
    r = x.fetchall()
    c.close()
    return r

@app.get("/urunler/listele")
def ul():
    c = get_db_connection()
    x = c.cursor()
    x.execute("SELECT * FROM Urunler ORDER BY urun_adi")
    r = x.fetchall()
    c.close()
    return r

@app.post("/bolumler/ekle")
def badd(d: BolumModel):
    c = get_db_connection()
    x = c.cursor()
    x.execute("INSERT INTO Bolumler(bolum_adi) VALUES(%s)", (d.bolum_adi,))
    c.commit()
    c.close()
    return {"msg": "ok"}

@app.post("/personel/ekle")
def padd(d: PersonelModel):
    c = get_db_connection()
    x = c.cursor()
    x.execute("INSERT INTO Personel(ad_soyad) VALUES(%s)", (d.ad_soyad,))
    c.commit()
    c.close()
    return {"msg": "ok"}

@app.post("/urunler/ekle")
def uadd(d: UrunModel):
    c = get_db_connection()
    x = c.cursor()
    x.execute("INSERT INTO Urunler(urun_adi,SKU,birim,tur,guvenlik_stogu) VALUES(%s,%s,%s,%s,%s)", 
              (d.urun_adi, d.sku, d.birim, d.tur, d.guvenlik_stogu))
    c.commit()
    c.close()
    return {"msg": "ok"}

@app.post("/giris")
def log(b: LoginModel):
    c = get_db_connection()
    x = c.cursor()
    x.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (b.kullanici_adi, b.sifre))
    u = x.fetchone()
    c.close()
    if u:
        return {"rol": u['rol'], "ad": u['kullanici_adi']}
    raise HTTPException(401, "Hatalı Giriş")

@app.post("/rapor/hareketler")
def h(f: RaporFiltreModel):
    c = get_db_connection()
    x = c.cursor()
    q = """
        SELECT 'GİRİŞ' as islem, giris_tarihi as t, miktar, belge_no 
        FROM StokGiris 
        WHERE giris_tarihi BETWEEN %s AND %s 
        UNION ALL 
        SELECT 'ÇIKIŞ' as islem, cikis_tarihi as t, miktar, aciklama 
        FROM StokCikis 
        WHERE cikis_tarihi BETWEEN %s AND %s 
        ORDER BY t DESC
    """
    x.execute(q, (f.baslangic_tarihi+"T00:00:00", f.bitis_tarihi+"T23:59:59", 
                  f.baslangic_tarihi+"T00:00:00", f.bitis_tarihi+"T23:59:59"))
    r = x.fetchall()
    c.close()
    return r