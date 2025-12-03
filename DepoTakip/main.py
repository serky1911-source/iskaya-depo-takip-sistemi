import os
import time
from datetime import datetime
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# --- GEMINI KÜTÜPHANESİ ---
import google.generativeai as genai

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- YAPAY ZEKA AYARLARI ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("UYARI: GEMINI_API_KEY bulunamadı! Yapay zeka çalışmayacak.")

# --- VERİTABANI BAĞLANTISI ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        # Yerel test için fallback (Opsiyonel)
        raise HTTPException(status_code=500, detail="Database URL yok")
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print("DB Hatası:", e)
        raise HTTPException(status_code=500, detail="DB Bağlantı Hatası")

# --- Tabloları Kur (Startup) ---
def veritabani_kur():
    retries = 5
    while retries > 0:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Tablolar (Değişiklik yok, aynen duruyor)
            cursor.execute("CREATE TABLE IF NOT EXISTS Kullanicilar (kullanici_id SERIAL PRIMARY KEY, kullanici_adi VARCHAR(50) UNIQUE NOT NULL, sifre VARCHAR(100) NOT NULL, rol VARCHAR(20) NOT NULL);")
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
class SoruModel(BaseModel): # YENİ
    soru: str

# --- HTML ---
@app.get("/", response_class=HTMLResponse)
def ana_sayfa():
    try:
        with open("index.html", "r", encoding="utf-8") as f: return f.read()
    except: return "<h1>index.html yok</h1>"

# --- YARDIMCI FONKSİYON: GÜNCEL STOK VERİSİNİ METNE ÇEVİR ---
def stok_verisini_al():
    conn = get_db_connection(); cur = conn.cursor()
    # Kimde ne var, hangi depoda ne var hepsini çekelim
    sorgu = """
    SELECT b.bolum_adi, u.urun_adi, 
           COALESCE(SUM(g.miktar), 0) - COALESCE((SELECT SUM(c.miktar) FROM StokCikis c WHERE c.urun_id = u.urun_id AND c.bolum_id = b.bolum_id), 0) as adet
    FROM Bolumler b 
    CROSS JOIN Urunler u 
    LEFT JOIN StokGiris g ON u.urun_id = g.urun_id AND b.bolum_id = g.bolum_id
    GROUP BY b.bolum_id, u.urun_adi, u.urun_id
    HAVING (COALESCE(SUM(g.miktar), 0) - COALESCE((SELECT SUM(c.miktar) FROM StokCikis c WHERE c.urun_id = u.urun_id AND c.bolum_id = b.bolum_id), 0)) > 0
    """
    cur.execute(sorgu)
    stoklar = cur.fetchall()
    
    # Zimmetleri de çekelim
    cur.execute("SELECT p.ad_soyad, u.urun_adi, z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id")
    zimmetler = cur.fetchall()
    conn.close()

    # Veriyi metne döküyoruz ki Yapay Zeka okuyabilsin
    metin = "ŞU ANKİ DEPO DURUMU:\n"
    for s in stoklar:
        metin += f"- {s['bolum_adi']} deposunda {s['adet']} adet {s['urun_adi']} var.\n"
    
    metin += "\nZİMMET DURUMU:\n"
    for z in zimmetler:
        metin += f"- {z['ad_soyad']} kişisinde {z['miktar']} adet {z['urun_adi']} zimmetli.\n"
        
    return metin

# --- API ENDPOINTLERİ ---

# YENİ: YAPAY ZEKA SOHBET ENDPOINT'İ
@app.post("/ai/sor")
def yapay_zekaya_sor(model: SoruModel):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="API Key tanımlı değil!")
    
    try:
        # 1. Güncel veriyi veritabanından çek
        context_data = stok_verisini_al()
        
        # 2. Gemini'ye talimat ver (Prompt Engineering)
        prompt = f"""
        Sen bir şantiye depo sorumlusu asistanısın. 
        Aşağıdaki GÜNCEL STOK VERİSİNE bakarak kullanıcının sorusunu cevapla.
        Sadece bu verideki bilgilere göre konuş. Veride olmayan bir şey sorulursa 'Bilgim yok' de.
        Kısa, net ve samimi cevap ver.
        
        VERİLER:
        {context_data}
        
        KULLANICI SORUSU:
        {model.soru}
        """
        
        # 3. Modele gönder (Flash Model - Hızlı ve Yüksek Limitli)
        model_ai = genai.GenerativeModel('gemini-1.5-flash')
        response = model_ai.generate_content(prompt)
        
        return {"cevap": response.text}
        
    except Exception as e:
        print("AI Hatası:", e)
        return {"cevap": "Şu an bağlantıda bir sorun var, daha sonra tekrar dener misin?"}

# (Diğer endpointler AYNI - yer kaplamasın diye kısalttım, önceki kodların aynısı)
@app.post("/giris")
def giris_yap(b: LoginModel):
    c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Kullanicilar WHERE kullanici_adi=%s AND sifre=%s",(b.kullanici_adi,b.sifre));u=cur.fetchone();c.close()
    if u: return {"mesaj":"ok","rol":u['rol'],"ad":u['kullanici_adi']}
    raise HTTPException(401,"Hata")
@app.get("/bolumler/listele")
def bl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Bolumler");r=cur.fetchall();c.close();return r
@app.get("/personel/listele")
def pl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Personel");r=cur.fetchall();c.close();return r
@app.get("/urunler/listele")
def ul(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT * FROM Urunler");r=cur.fetchall();c.close();return r
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
@app.get("/rapor/genel-stok")
def gs(): 
    c=get_db_connection();cur=c.cursor();
    cur.execute("SELECT b.bolum_adi, u.urun_adi, u.SKU, u.tur, u.guvenlik_stogu, COALESCE(SUM(g.miktar),0)-COALESCE((SELECT SUM(mk) FROM StokCikis sc2 LEFT JOIN (SELECT cikis_id, miktar as mk FROM StokCikis) sc3 ON sc2.cikis_id=sc3.cikis_id WHERE sc2.urun_id=u.urun_id AND sc2.bolum_id=b.bolum_id),0) as mevcut_stok FROM Bolumler b CROSS JOIN Urunler u LEFT JOIN StokGiris g ON u.urun_id=g.urun_id AND b.bolum_id=g.bolum_id GROUP BY b.bolum_id,u.urun_id,u.urun_adi,u.SKU,u.tur,u.guvenlik_stogu HAVING (COALESCE(SUM(g.miktar),0)-COALESCE((SELECT SUM(c.miktar) FROM StokCikis c WHERE c.urun_id=u.urun_id AND c.bolum_id=b.bolum_id),0))>0 ORDER BY b.bolum_adi")
    r=cur.fetchall();c.close()
    for v in r: v['uyari']=(v['tur']=='Sarf' and v['mevcut_stok']<=v['guvenlik_stogu'])
    return r
@app.get("/rapor/zimmetler")
def zl(): c=get_db_connection();cur=c.cursor();cur.execute("SELECT z.verilis_tarihi,p.ad_soyad,u.urun_adi,u.SKU,z.miktar FROM Zimmetler z JOIN Personel p ON z.personel_id=p.personel_id JOIN Urunler u ON z.urun_id=u.urun_id");r=cur.fetchall();c.close();return r
@app.post("/rapor/hareketler")
def hd(f:RaporFiltreModel):
    c=get_db_connection();cur=c.cursor()
    q="SELECT 'GİRİŞ' as islem_tipi,g.giris_tarihi as tarih,b.bolum_adi,u.urun_adi,g.miktar,g.belge_no as aciklama FROM StokGiris g JOIN Bolumler b ON g.bolum_id=b.bolum_id JOIN Urunler u ON g.urun_id=u.urun_id WHERE g.giris_tarihi BETWEEN %s AND %s UNION ALL SELECT 'ÇIKIŞ' as islem_tipi,c.cikis_tarihi as tarih,b.bolum_adi,u.urun_adi,c.miktar,c.aciklama FROM StokCikis c JOIN Bolumler b ON c.bolum_id=b.bolum_id JOIN Urunler u ON c.urun_id=u.urun_id WHERE c.cikis_tarihi BETWEEN %s AND %s ORDER BY tarih DESC"
    t1=f"{f.baslangic_tarihi}T00:00:00";t2=f"{f.bitis_tarihi}T23:59:59"
    cur.execute(q,(t1,t2,t1,t2));r=cur.fetchall();c.close()
    if f.bolum_id!=0: return [x for x in r if x['bolum_adi']==(next((y['bolum_adi'] for y in bl() if y['bolum_id']==f.bolum_id),None))]
    return r