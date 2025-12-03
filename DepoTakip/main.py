import os
import time
from datetime import datetime
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse # YENİ: HTML göstermek için
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BULUT VERİTABANI BAĞLANTISI ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise HTTPException(status_code=500, detail="Veritabanı URL bulunamadı!")
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print("Veritabanı Hatası:", e)
        raise HTTPException(status_code=500, detail="Veritabanına bağlanılamadı.")

# --- Tabloları Kur ---
def veritabani_kur():
    retries = 5
    while retries > 0:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("CREATE TABLE IF NOT EXISTS Kullanicilar (kullanici_id SERIAL PRIMARY KEY, kullanici_adi VARCHAR(50) UNIQUE NOT NULL, sifre VARCHAR(100) NOT NULL, rol VARCHAR(20) NOT NULL);")
            
            # Admin ve Müdür Ekleme
            cursor.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi='admin'")
            if not cursor.fetchone(): cursor.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('admin', 'admin123', 'admin')")
            
            cursor.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi='mudur'")
            if not cursor.fetchone(): cursor.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici')")

            cursor.execute("CREATE TABLE IF NOT EXISTS Bolumler (bolum_id SERIAL PRIMARY KEY, bolum_adi VARCHAR(100) UNIQUE NOT NULL);")
            cursor.execute("CREATE TABLE IF NOT EXISTS Personel (personel_id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100) NOT NULL);")
            cursor.execute("CREATE TABLE IF NOT EXISTS Urunler (urun_id SERIAL PRIMARY KEY, urun_adi VARCHAR(200) NOT NULL, SKU VARCHAR(50) UNIQUE NOT NULL, birim VARCHAR(20) NOT NULL, tur VARCHAR(20) NOT NULL, guvenlik_stogu INTEGER DEFAULT 0);")
            cursor.execute("CREATE TABLE IF NOT EXISTS StokGiris (giris_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), bolum_id INTEGER REFERENCES Bolumler(bolum_id), miktar REAL NOT NULL, giris_tarihi TIMESTAMP NOT NULL, belge_no VARCHAR(100));")
            cursor.execute("CREATE TABLE IF NOT EXISTS StokCikis (cikis_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), bolum_id INTEGER REFERENCES Bolumler(bolum_id), miktar REAL NOT NULL, cikis_tarihi TIMESTAMP NOT NULL, sevkiyat_no VARCHAR(100), aciklama TEXT);")
            cursor.execute("CREATE TABLE IF NOT EXISTS Zimmetler (zimmet_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), personel_id INTEGER REFERENCES Personel(personel_id), miktar REAL NOT NULL, verilis_tarihi TIMESTAMP NOT NULL);")

            conn.commit(); conn.close()
            print("Veritabanı hazır."); break
        except Exception as e:
            print(f"Veritabanı bekleniyor... {e}"); retries -= 1; time.sleep(2)

# --- Modeller ---
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
class RaporFiltreModel(BaseModel):
    baslangic_tarihi: str
    bitis_tarihi: str
    bolum_id: int = 0

@app.on_event("startup")
def startup_event(): veritabani_kur()

# --- YENİ: HTML Dosyasını Gösteren Ana Sayfa ---
@app.get("/", response_class=HTMLResponse)
def ana_sayfa():
    # index.html dosyasını okuyup ekrana basar
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Hata: index.html dosyası bulunamadı!</h1>"

# --- API ENDPOINTLERİ ---
@app.post("/giris")
def giris_yap(bilgi: LoginModel):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (bilgi.kullanici_adi, bilgi.sifre))
    user = cur.fetchone(); conn.close()
    if user: return {"mesaj": "Giriş Başarılı", "rol": user['rol'], "ad": user['kullanici_adi']}
    raise HTTPException(status_code=401, detail="Hatalı giriş")

@app.get("/bolumler/listele")
def bolumleri_getir():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("SELECT * FROM Bolumler"); res = cur.fetchall(); conn.close(); return res
@app.get("/personel/listele")
def personelleri_getir():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("SELECT * FROM Personel"); res = cur.fetchall(); conn.close(); return res
@app.get("/urunler/listele")
def urunleri_getir():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("SELECT * FROM Urunler"); res = cur.fetchall(); conn.close(); return res

@app.post("/bolumler/ekle")
def bolum_ekle(b: BolumModel):
    conn = get_db_connection(); cur = conn.cursor(); 
    try: cur.execute("INSERT INTO Bolumler (bolum_adi) VALUES (%s)", (b.bolum_adi,)); conn.commit()
    except: pass
    conn.close(); return {"msg": "ok"}
@app.post("/personel/ekle")
def personel_ekle(p: PersonelModel):
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("INSERT INTO Personel (ad_soyad) VALUES (%s)", (p.ad_soyad,)); conn.commit(); conn.close(); return {"msg": "ok"}
@app.post("/urunler/ekle")
def urun_ekle(u: UrunModel):
    conn = get_db_connection(); cur = conn.cursor(); 
    try: cur.execute("INSERT INTO Urunler (urun_adi, SKU, birim, tur, guvenlik_stogu) VALUES (%s,%s,%s,%s,%s)", (u.urun_adi, u.sku, u.birim, u.tur, u.guvenlik_stogu)); conn.commit()
    except: pass
    conn.close(); return {"msg": "ok"}

@app.post("/stok/giris")
def stok_giris(i: IslemModel):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (i.sku,)); urun = cur.fetchone()
    cur.execute("INSERT INTO StokGiris (urun_id, bolum_id, miktar, giris_tarihi, belge_no) VALUES (%s,%s,%s,%s,%s)", (urun['urun_id'], i.bolum_id, i.miktar, datetime.now(), i.belge_no))
    conn.commit(); conn.close(); return {"msg": "ok"}
@app.post("/stok/cikis")
def stok_cikis(i: IslemModel):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (i.sku,)); urun = cur.fetchone()
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, sevkiyat_no, aciklama) VALUES (%s,%s,%s,%s,%s,%s)", (urun['urun_id'], i.bolum_id, i.miktar, datetime.now(), i.belge_no, "Tüketim"))
    conn.commit(); conn.close(); return {"msg": "ok"}
@app.post("/stok/transfer")
def transfer(t: TransferModel):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (t.sku,)); urun = cur.fetchone(); now = datetime.now()
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, aciklama) VALUES (%s,%s,%s,%s,%s)", (urun['urun_id'], t.cikis_bolum_id, t.miktar, now, f"Transfer->{t.giris_bolum_id}"))
    cur.execute("INSERT INTO StokGiris (urun_id, bolum_id, miktar, giris_tarihi, belge_no) VALUES (%s,%s,%s,%s,%s)", (urun['urun_id'], t.giris_bolum_id, t.miktar, now, f"Transfer<-{t.cikis_bolum_id}"))
    conn.commit(); conn.close(); return {"msg": "ok"}
@app.post("/zimmet/ver")
def zimmet(z: ZimmetModel):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (z.sku,)); urun = cur.fetchone(); now = datetime.now()
    cur.execute("INSERT INTO StokCikis (urun_id, bolum_id, miktar, cikis_tarihi, aciklama) VALUES (%s,%s,%s,%s,%s)", (urun['urun_id'], z.bolum_id, z.miktar, now, f"Zimmet->{z.personel_id}"))
    cur.execute("INSERT INTO Zimmetler (urun_id, personel_id, miktar, verilis_tarihi) VALUES (%s,%s,%s,%s)", (urun['urun_id'], z.personel_id, z.miktar, now))
    conn.commit(); conn.close(); return {"msg": "ok"}

@app.get("/rapor/genel-stok")
def genel_stok():
    conn = get_db_connection(); cur = conn.cursor()
    sorgu = "SELECT b.bolum_adi, u.urun_adi, u.SKU, u.tur, u.guvenlik_stogu, COALESCE(SUM(g.miktar), 0) - COALESCE((SELECT SUM(c.miktar) FROM StokCikis c WHERE c.urun_id = u.urun_id AND c.bolum_id = b.bolum_id), 0) as mevcut_stok FROM Bolumler b CROSS JOIN Urunler u LEFT JOIN StokGiris g ON u.urun_id = g.urun_id AND b.bolum_id = g.bolum_id GROUP BY b.bolum_id, u.urun_adi, u.SKU, u.tur, u.guvenlik_stogu, u.urun_id HAVING (COALESCE(SUM(g.miktar), 0) - COALESCE((SELECT SUM(c.miktar) FROM StokCikis c WHERE c.urun_id = u.urun_id AND c.bolum_id = b.bolum_id), 0)) > 0 ORDER BY b.bolum_adi, u.urun_adi"
    cur.execute(sorgu); veriler = cur.fetchall()
    for v in veriler: v['uyari'] = (v['tur'] == 'Sarf' and v['mevcut_stok'] <= v['guvenlik_stogu'])
    conn.close(); return veriler

@app.get("/rapor/zimmetler")
def zimmet_listesi():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("SELECT z.verilis_tarihi, p.ad_soyad, u.urun_adi, u.SKU, z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id"); res = cur.fetchall(); conn.close(); return res

@app.post("/rapor/hareketler")
def hareket_dokumu(f: RaporFiltreModel):
    conn = get_db_connection(); cur = conn.cursor()
    sorgu = "SELECT 'GİRİŞ' as islem_tipi, g.giris_tarihi as tarih, b.bolum_adi, u.urun_adi, g.miktar, g.belge_no as aciklama FROM StokGiris g JOIN Bolumler b ON g.bolum_id = b.bolum_id JOIN Urunler u ON g.urun_id = u.urun_id WHERE g.giris_tarihi BETWEEN %s AND %s UNION ALL SELECT 'ÇIKIŞ' as islem_tipi, c.cikis_tarihi as tarih, b.bolum_adi, u.urun_adi, c.miktar, c.aciklama FROM StokCikis c JOIN Bolumler b ON c.bolum_id = b.bolum_id JOIN Urunler u ON c.urun_id = u.urun_id WHERE c.cikis_tarihi BETWEEN %s AND %s ORDER BY tarih DESC"
    t1 = f"{f.baslangic_tarihi}T00:00:00"; t2 = f"{f.bitis_tarihi}T23:59:59"
    cur.execute(sorgu, (t1, t2, t1, t2)); tum_hareketler = cur.fetchall()
    if f.bolum_id != 0:
        cur.execute("SELECT bolum_adi FROM Bolumler WHERE bolum_id = %s", (f.bolum_id,)); bolum = cur.fetchone()
        if bolum: tum_hareketler = [h for h in tum_hareketler if h['bolum_adi'] == bolum['bolum_adi']]
    conn.close(); return tum_hareketler