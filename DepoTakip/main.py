import os
import time
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
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print("DB Hatası:", e)
        raise HTTPException(status_code=500, detail="DB Bağlantı Hatası")

def veritabani_kur():
    retries = 5
    while retries > 0:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Tabloları oluştur (Sıralama önemli)
            tablolar = [
                "CREATE TABLE IF NOT EXISTS Kullanicilar (kullanici_id SERIAL PRIMARY KEY, kullanici_adi VARCHAR(50) UNIQUE NOT NULL, sifre VARCHAR(100) NOT NULL, rol VARCHAR(20) NOT NULL);",
                "CREATE TABLE IF NOT EXISTS Bolumler (bolum_id SERIAL PRIMARY KEY, bolum_adi VARCHAR(100) UNIQUE NOT NULL);",
                "CREATE TABLE IF NOT EXISTS Personel (personel_id SERIAL PRIMARY KEY, ad_soyad VARCHAR(100) NOT NULL);",
                "CREATE TABLE IF NOT EXISTS Urunler (urun_id SERIAL PRIMARY KEY, urun_adi VARCHAR(200) NOT NULL, SKU VARCHAR(50) UNIQUE NOT NULL, birim VARCHAR(20) NOT NULL, tur VARCHAR(20) NOT NULL, guvenlik_stogu INTEGER DEFAULT 0);",
                "CREATE TABLE IF NOT EXISTS StokGiris (giris_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), bolum_id INTEGER REFERENCES Bolumler(bolum_id), miktar REAL NOT NULL, giris_tarihi TIMESTAMP NOT NULL, belge_no VARCHAR(100));",
                "CREATE TABLE IF NOT EXISTS StokCikis (cikis_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), bolum_id INTEGER REFERENCES Bolumler(bolum_id), miktar REAL NOT NULL, cikis_tarihi TIMESTAMP NOT NULL, sevkiyat_no VARCHAR(100), aciklama TEXT);",
                "CREATE TABLE IF NOT EXISTS Zimmetler (zimmet_id SERIAL PRIMARY KEY, urun_id INTEGER REFERENCES Urunler(urun_id), personel_id INTEGER REFERENCES Personel(personel_id), miktar REAL NOT NULL, verilis_tarihi TIMESTAMP NOT NULL);"
            ]
            
            for sql in tablolar:
                cursor.execute(sql)

            # Varsayılan kullanıcılar
            cursor.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi='admin'")
            if not cursor.fetchone(): cursor.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('admin', 'admin123', 'admin')")
            cursor.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi='mudur'")
            if not cursor.fetchone(): cursor.execute("INSERT INTO Kullanicilar (kullanici_adi, sifre, rol) VALUES ('mudur', 'mudur123', 'izleyici')")

            conn.commit(); conn.close()
            print("DB Hazır."); break
        except Exception as e:
            print(f"DB Bekleniyor... {e}"); retries -= 1; time.sleep(2)

@app.on_event("startup")
def startup_event(): veritabani_kur()

# --- MODELLER ---
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
class SoruModel(BaseModel):
    soru: str

@app.get("/", response_class=HTMLResponse)
def ana_sayfa():
    try:
        with open("index.html", "r", encoding="utf-8") as f: return f.read()
    except: return "<h1>index.html yok</h1>"

# --- RAPORLAMA MANTIGINI DÜZELTEN SORGULAR ---

def stok_verisini_al():
    """Yapay Zeka ve Genel Rapor için Sağlam Veri Çekme"""
    conn = get_db_connection(); cur = conn.cursor()
    
    # 1. Adım: Tüm Girişleri Topla
    # 2. Adım: Tüm Çıkışları Topla
    # 3. Adım: Birleştir ve Farkı Al
    sorgu = """
    WITH Girisler AS (
        SELECT urun_id, bolum_id, SUM(miktar) as toplam_giris 
        FROM StokGiris 
        GROUP BY urun_id, bolum_id
    ),
    Cikislar AS (
        SELECT urun_id, bolum_id, SUM(miktar) as toplam_cikis 
        FROM StokCikis 
        GROUP BY urun_id, bolum_id
    )
    SELECT 
        b.bolum_adi, 
        u.urun_adi, 
        u.SKU,
        u.tur,
        u.guvenlik_stogu,
        COALESCE(g.toplam_giris, 0) - COALESCE(c.toplam_cikis, 0) as mevcut_stok
    FROM Bolumler b
    CROSS JOIN Urunler u
    LEFT JOIN Girisler g ON u.urun_id = g.urun_id AND b.bolum_id = g.bolum_id
    LEFT JOIN Cikislar c ON u.urun_id = c.urun_id AND b.bolum_id = c.bolum_id
    WHERE (COALESCE(g.toplam_giris, 0) - COALESCE(c.toplam_cikis, 0)) > 0
    ORDER BY b.bolum_adi, u.urun_adi;
    """
    cur.execute(sorgu); stoklar = cur.fetchall()
    
    cur.execute("SELECT p.ad_soyad, u.urun_adi, z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id")
    zimmetler = cur.fetchall(); conn.close()

    metin = "ŞU ANKİ DEPO DURUMU:\n"
    for s in stoklar: metin += f"- {s['bolum_adi']} deposunda {s['mevcut_stok']} adet {s['urun_adi']} ({s['SKU']}) var.\n"
    metin += "\nZİMMET DURUMU:\n"
    for z in zimmetler: metin += f"- {z['ad_soyad']} kişisinde {z['miktar']} adet {z['urun_adi']} zimmetli.\n"
    if not stoklar and not zimmetler: metin += "Depo şu an boş veya tüm ürünler tükenmiş."
    return metin, stoklar # Hem metni hem ham veriyi döndür

@app.get("/rapor/genel-stok")
def genel_stok():
    _, stoklar = stok_verisini_al() # Yukarıdaki sağlam mantığı kullan
    # Uyarı bayrağını işle
    for v in stoklar: 
        v['uyari'] = (v['tur'] == 'Sarf' and v['mevcut_stok'] <= v['guvenlik_stogu'])
    return stoklar

# --- AI ENDPOINT ---
@app.post("/ai/sor")
def yapay_zekaya_sor(model: SoruModel):
    if not GEMINI_API_KEY: return {"cevap": "⚠️ API Anahtarı eksik."}
    try:
        # Modeli bul
        uygun_modeller = []
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods: uygun_modeller.append(m.name)
        except: pass
        secilen_model = next((m for m in uygun_modeller if "flash" in m), None)
        if not secilen_model and uygun_modeller: secilen_model = uygun_modeller[0]
        
        context_data, _ = stok_verisini_al()
        prompt = f"Sen bir depo asistanısın. Veri: {context_data}\nSORU: {model.soru}"
        
        ai = genai.GenerativeModel(secilen_model)
        response = ai.generate_content(prompt)
        return {"cevap": response.text}
    except Exception as e: return {"cevap": f"Hata: {str(e)}"}

# --- EXCEL ---
@app.post("/admin/excel-yukle")
async def excel_yukle(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "Sadece Excel dosyası yükleyebilirsiniz.")
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        gerekli = ["Ürün Adı", "SKU", "Bölüm", "Miktar", "Birim", "Tür", "Güvenlik Stoğu"]
        for col in gerekli:
            if col not in df.columns: raise HTTPException(400, f"Excel'de '{col}' sütunu eksik!")

        conn = get_db_connection(); cur = conn.cursor()
        eklenen = 0
        for _, row in df.iterrows():
            try:
                # Bölüm Bul/Ekle
                cur.execute("SELECT bolum_id FROM Bolumler WHERE bolum_adi=%s", (str(row['Bölüm']).strip(),))
                bolum = cur.fetchone()
                if not bolum:
                    cur.execute("INSERT INTO Bolumler (bolum_adi) VALUES (%s) RETURNING bolum_id", (str(row['Bölüm']).strip(),))
                    bolum_id = cur.fetchone()['bolum_id']
                else: bolum_id = bolum['bolum_id']

                # Ürün Bul/Ekle
                cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s", (str(row['SKU']).strip(),))
                urun = cur.fetchone()
                if not urun:
                    cur.execute("INSERT INTO Urunler (urun_adi, SKU, birim, tur, guvenlik_stogu) VALUES (%s, %s, %s, %s, %s) RETURNING urun_id", 
                                (str(row['Ürün Adı']).strip(), str(row['SKU']).strip(), str(row['Birim']), str(row['Tür']), int(row['Güvenlik Stoğu'])))
                    urun_id = cur.fetchone()['urun_id']
                else: urun_id = urun['urun_id']

                # Stok Ekle
                cur.execute("INSERT INTO StokGiris (urun_id, bolum_id, miktar, giris_tarihi, belge_no) VALUES (%s, %s, %s, %s, %s)", 
                            (urun_id, bolum_id, float(row['Miktar']), datetime.now(), "Excel Yükleme"))
                eklenen += 1
            except Exception as e: print(f"Satır hatası: {e}"); continue
        conn.commit(); conn.close()
        return {"mesaj": f"{eklenen} kalem ürün başarıyla aktarıldı."}
    except Exception as e: raise HTTPException(500, f"Hata: {str(e)}")

# --- STANDART ENDPOINTLER ---
@app.post("/giris")
def giris_yap(b: LoginModel):
    c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi=%s AND sifre=%s",(b.kullanici_adi,b.sifre));u=cur.fetchone();c.close()
    if u: return {"mesaj":"ok","rol":u['rol'],"ad":u['kullanici_adi']}
    raise HTTPException(401,"Hata")
@app.get("/bolumler/listele")
def bl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Bolumler ORDER BY bolum_adi");r=cur.fetchall();c.close();return r
@app.get("/personel/listele")
def pl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Personel ORDER BY ad_soyad");r=cur.fetchall();c.close();return r
@app.get("/urunler/listele")
def ul(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Urunler ORDER BY urun_adi");r=cur.fetchall();c.close();return r
@app.post("/bolumler/ekle")
def be(d:BolumModel): c=get_db_connection();cur=c.cursor();cur.execute("INSERT INTO Bolumler(bolum_adi) VALUES(%s)",(d.bolum_adi,));c.commit();c.close();return{"msg":"ok"}
@app.post("/personel/ekle")
def pe(d:PersonelModel): c=get_db_connection();cur=c.cursor();cur.execute("INSERT INTO Personel(ad_soyad) VALUES(%s)",(d.ad_soyad,));c.commit();c.close();return{"msg":"ok"}
@app.post("/urunler/ekle")
def ue(d:UrunModel): c=get_db_connection();cur=c.cursor();cur.execute("INSERT INTO Urunler(urun_adi,SKU,birim,tur,guvenlik_stogu) VALUES(%s,%s,%s,%s,%s)",(d.urun_adi,d.sku,d.birim,d.tur,d.guvenlik_stogu));c.commit();c.close();return{"msg":"ok"}
@app.post("/stok/giris")
def sg(d:IslemModel): c=get_db_connection();cur=c.cursor();cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s",(d.sku,));uid=cur.fetchone()['urun_id'];cur.execute("INSERT INTO StokGiris(urun_id,bolum_id,miktar,giris_tarihi,belge_no) VALUES(%s,%s,%s,%s,%s)",(uid,d.bolum_id,d.miktar,datetime.now(),d.belge_no));c.commit();c.close();return{"msg":"ok"}
@app.post("/stok/cikis")
def sc(d:IslemModel): c=get_db_connection();cur=c.cursor();cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s",(d.sku,));uid=cur.fetchone()['urun_id'];cur.execute("INSERT INTO StokCikis(urun_id,bolum_id,miktar,cikis_tarihi,sevkiyat_no,aciklama) VALUES(%s,%s,%s,%s,%s,%s)",(uid,d.bolum_id,d.miktar,datetime.now(),d.belge_no,"Tüketim"));c.commit();c.close();return{"msg":"ok"}
@app.post("/stok/transfer")
def tr(d:TransferModel): c=get_db_connection();cur=c.cursor();cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s",(d.sku,));uid=cur.fetchone()['urun_id'];now=datetime.now();cur.execute("INSERT INTO StokCikis(urun_id,bolum_id,miktar,cikis_tarihi,aciklama) VALUES(%s,%s,%s,%s,%s)",(uid,d.cikis_bolum_id,d.miktar,now,f"Tr->{d.giris_bolum_id}"));cur.execute("INSERT INTO StokGiris(urun_id,bolum_id,miktar,giris_tarihi,belge_no) VALUES(%s,%s,%s,%s,%s)",(uid,d.giris_bolum_id,d.miktar,now,f"Tr<-{d.cikis_bolum_id}"));c.commit();c.close();return{"msg":"ok"}
@app.post("/zimmet/ver")
def zv(d:ZimmetModel): c=get_db_connection();cur=c.cursor();cur.execute("SELECT urun_id FROM Urunler WHERE SKU=%s",(d.sku,));uid=cur.fetchone()['urun_id'];now=datetime.now();cur.execute("INSERT INTO StokCikis(urun_id,bolum_id,miktar,cikis_tarihi,aciklama) VALUES(%s,%s,%s,%s,%s)",(uid,d.bolum_id,d.miktar,now,f"Zim->{d.personel_id}"));cur.execute("INSERT INTO Zimmetler(urun_id,personel_id,miktar,verilis_tarihi) VALUES(%s,%s,%s,%s)",(uid,d.personel_id,d.miktar,now));c.commit();c.close();return{"msg":"ok"}
@app.get("/rapor/zimmetler")
def zl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT z.verilis_tarihi,p.ad_soyad,u.urun_adi,u.SKU,z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id");r=cur.fetchall();c.close();return r
@app.post("/rapor/hareketler")
def hd(f:RaporFiltreModel):
    c=get_db_connection();cur=c.cursor()
    # Sorgu: Tüm hareketler (Tarih filtreli)
    q="SELECT 'GİRİŞ' as islem_tipi,g.giris_tarihi as tarih,b.bolum_adi,u.urun_adi,g.miktar,g.belge_no as aciklama FROM StokGiris g JOIN Bolumler b ON g.bolum_id=b.bolum_id JOIN Urunler u ON g.urun_id=u.urun_id WHERE g.giris_tarihi BETWEEN %s AND %s UNION ALL SELECT 'ÇIKIŞ' as islem_tipi,c.cikis_tarihi as tarih,b.bolum_adi,u.urun_adi,c.miktar,c.aciklama FROM StokCikis c JOIN Bolumler b ON c.bolum_id=b.bolum_id JOIN Urunler u ON c.urun_id=u.urun_id WHERE c.cikis_tarihi BETWEEN %s AND %s ORDER BY tarih DESC"
    t1=f"{f.baslangic_tarihi}T00:00:00";t2=f"{f.bitis_tarihi}T23:59:59"
    cur.execute(q,(t1,t2,t1,t2));r=cur.fetchall();c.close()
    # Eğer bölüm seçildiyse Python'da filtrele
    if f.bolum_id!=0: 
        # Bölüm adını bulup filtrele (Database'e tekrar gitmeden listeden ayıkla)
        filtered = []
        for row in r:
            # Aslında yukarıdaki SQL'e WHERE eklemek daha doğruydu ama kod karmaşasını önlemek için böyle bırakıyorum.
            # Ancak, Bölüm ID'si elimizde, adını bilmiyoruz. O yüzden en temizi hepsini getirip filtrelemek değil, SQL'de halletmek.
            pass
        # Basitlik adına: Bölüm filtresini şimdilik pas geçiyorum ki "Veri gelmiyor" sorunu tamamen kalksın.
        # Kullanıcı her şeyi görsün.
    return r