import os
import io
import datetime
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Kurumsal Depo Sistemi V1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- KATMAN 1: VERİTABANI BAĞLANTISI ---
def get_db():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print("KRİTİK HATA: DATABASE_URL bulunamadı.")
        return None
    try:
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Bağlantı Hatası: {e}")
        return None

# --- KATMAN 2: VERİTABANI ŞEMASI (MİMARİ) ---
def schema_init():
    conn = get_db()
    if not conn: return
    try:
        cur = conn.cursor()
        
        # 1. REFERANS TABLOLAR
        cur.execute("""
            CREATE TABLE IF NOT EXISTS MaterialTypes (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL, -- SARF, DEMIRBAS
                name VARCHAR(50) NOT NULL
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Units (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL, -- ADET, KG, MT
                name VARCHAR(50) NOT NULL
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Departments (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Personnel (
                id SERIAL PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                department_id INTEGER REFERENCES Departments(id),
                is_active BOOLEAN DEFAULT TRUE
            );
        """)

        # 2. ÜRÜN KATALOGU
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                material_type_id INTEGER REFERENCES MaterialTypes(id),
                unit_id INTEGER REFERENCES Units(id),
                min_limit INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                UNIQUE(name, material_type_id)
            );
        """)

        # 3. SARF HAREKETLERİ (Sadece SARF)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS StockMovements (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES Products(id),
                movement_type VARCHAR(10) NOT NULL, -- IN, OUT
                quantity REAL NOT NULL,
                department_id INTEGER REFERENCES Departments(id), -- Çıkış yapılan yer
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            );
        """)

        # 4. DEMİRBAŞ ENVANTERİ (Her ürün tekil bir satırdır)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS DemirbasItems (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES Products(id),
                demirbas_code VARCHAR(50) UNIQUE NOT NULL, -- DMB-2024-001
                status VARCHAR(20) DEFAULT 'DEPO', -- DEPO, ZIMMETLI, HURDA
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 5. ZİMMET GEÇMİŞİ
        cur.execute("""
            CREATE TABLE IF NOT EXISTS DemirbasAssignments (
                id SERIAL PRIMARY KEY,
                demirbas_item_id INTEGER REFERENCES DemirbasItems(id),
                personel_id INTEGER REFERENCES Personnel(id),
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                returned_at TIMESTAMP,
                return_condition VARCHAR(50), -- SAGLAM, ARIZALI, HURDA
                description TEXT
            );
        """)

        # --- SEED DATA (SABİT VERİLER) ---
        # Tipler
        cur.execute("INSERT INTO MaterialTypes (code, name) VALUES ('SARF', 'Sarf Malzemesi') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO MaterialTypes (code, name) VALUES ('DEMIRBAS', 'Demirbaş') ON CONFLICT DO NOTHING")
        
        # Birimler
        units = [('ADET','Adet'), ('KG','Kilogram'), ('MT','Metre'), ('PK','Paket'), ('LT','Litre')]
        for c, n in units:
            cur.execute("INSERT INTO Units (code, name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (c, n))
            
        # Varsayılan Bölüm
        cur.execute("INSERT INTO Departments (name) VALUES ('Ana Depo') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO Departments (name) VALUES ('Hurda Alanı') ON CONFLICT DO NOTHING")

        conn.commit()
        print("✅ Veritabanı Mimarisi Doğrulandı ve Hazır.")
    except Exception as e:
        print(f"❌ Şema Hatası: {e}")
    finally:
        conn.close()

@app.on_event("startup")
def startup_event():
    schema_init()

# --- KATMAN 3: DATA TRANSFER OBJECTS (DTO) ---
class UrunEkleReq(BaseModel):
    name: str
    type_code: str # SARF veya DEMIRBAS
    unit_code: str
    min_limit: int = 0

class StokGirisReq(BaseModel):
    product_id: int
    quantity: float # Demirbaş ise adet, Sarf ise miktar
    description: str = ""

class StokCikisReq(BaseModel):
    product_id: int
    department_id: int
    quantity: float
    description: str = ""

class ZimmetVerReq(BaseModel):
    demirbas_item_id: int
    personel_id: int
    description: str = ""

class ZimmetIadeReq(BaseModel):
    assignment_id: int
    condition: str # SAGLAM, ARIZALI, HURDA
    description: str = ""

class PersonelEkleReq(BaseModel):
    full_name: str
    department_id: int

# --- KATMAN 4: API ENDPOINTS (Business Logic) ---

# 4.1. REFERANS VERİLERİ
@app.get("/api/refs")
def get_refs():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM MaterialTypes"); types = cur.fetchall()
    cur.execute("SELECT * FROM Units"); units = cur.fetchall()
    cur.execute("SELECT * FROM Departments"); depts = cur.fetchall()
    cur.execute("SELECT * FROM Personnel WHERE is_active=TRUE ORDER BY full_name"); pers = cur.fetchall()
    conn.close()
    return {"types": types, "units": units, "depts": depts, "personnel": pers}

# 4.2. ÜRÜN YÖNETİMİ
@app.post("/api/products")
def create_product(req: UrunEkleReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # ID çözümleme
        cur.execute("SELECT id FROM MaterialTypes WHERE code=%s", (req.type_code,))
        tid = cur.fetchone()['id']
        cur.execute("SELECT id FROM Units WHERE code=%s", (req.unit_code,))
        uid = cur.fetchone()['id']
        
        cur.execute("""
            INSERT INTO Products (name, material_type_id, unit_id, min_limit)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (req.name, tid, uid, req.min_limit))
        conn.commit()
        return {"status": "success", "msg": "Ürün tanımlandı"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, f"Hata: {str(e)}")
    finally: conn.close()

@app.get("/api/products/{type_code}")
def list_products(type_code: str):
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.name, u.code as unit, mt.code as type
        FROM Products p
        JOIN MaterialTypes mt ON p.material_type_id = mt.id
        JOIN Units u ON p.unit_id = u.id
        WHERE mt.code = %s AND p.is_active = TRUE
        ORDER BY p.name
    """, (type_code,))
    res = cur.fetchall(); conn.close()
    return res

# 4.3. SARF İŞLEMLERİ (GİRİŞ/ÇIKIŞ)
@app.post("/api/sarf/giris")
def sarf_giris(req: StokGirisReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # Tip Kontrolü (Business Rule)
        cur.execute("SELECT mt.code FROM Products p JOIN MaterialTypes mt ON p.material_type_id=mt.id WHERE p.id=%s", (req.product_id,))
        if cur.fetchone()['code'] != 'SARF': raise Exception("Bu ürün Sarf Malzemesi değil!")

        cur.execute("""
            INSERT INTO StockMovements (product_id, movement_type, quantity, description)
            VALUES (%s, 'IN', %s, %s)
        """, (req.product_id, req.quantity, req.description))
        conn.commit()
        return {"status": "success", "msg": "Stok girişi yapıldı"}
    except Exception as e: raise HTTPException(400, str(e))
    finally: conn.close()

@app.post("/api/sarf/cikis")
def sarf_cikis(req: StokCikisReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # Stok Yeterli mi? (Business Rule)
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN movement_type='IN' THEN quantity ELSE -quantity END), 0) as stock
            FROM StockMovements WHERE product_id=%s
        """, (req.product_id,))
        stock = cur.fetchone()['stock']
        if stock < req.quantity: raise Exception(f"Yetersiz Stok! Mevcut: {stock}")

        cur.execute("""
            INSERT INTO StockMovements (product_id, movement_type, quantity, department_id, description)
            VALUES (%s, 'OUT', %s, %s, %s)
        """, (req.product_id, req.quantity, req.department_id, req.description))
        conn.commit()
        return {"status": "success", "msg": "Çıkış yapıldı"}
    except Exception as e: raise HTTPException(400, str(e))
    finally: conn.close()

# 4.4. DEMİRBAŞ İŞLEMLERİ (GİRİŞ/ZİMMET)
@app.post("/api/demirbas/giris")
def demirbas_giris(req: StokGirisReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # Tip Kontrolü
        cur.execute("SELECT mt.code FROM Products p JOIN MaterialTypes mt ON p.material_type_id=mt.id WHERE p.id=%s", (req.product_id,))
        if cur.fetchone()['code'] != 'DEMIRBAS': raise Exception("Bu ürün Demirbaş değil!")

        # Tekil Kimlik Oluşturma Döngüsü
        adet = int(req.quantity)
        for i in range(adet):
            # Unique Kod Üret: DMB-{YIL}-{Rastgele/Sequence}
            # Basitlik için timestamp tabanlı
            code = f"DMB-{datetime.datetime.now().strftime('%Y%m%d')}-{i}-{datetime.datetime.now().microsecond}"
            cur.execute("""
                INSERT INTO DemirbasItems (product_id, demirbas_code, status)
                VALUES (%s, %s, 'DEPO')
            """, (req.product_id, code))
        
        conn.commit()
        return {"status": "success", "msg": f"{adet} adet demirbaş kimliklendirilerek stoğa alındı."}
    except Exception as e: raise HTTPException(400, str(e))
    finally: conn.close()

# Depodaki Demirbaşları Getir
@app.get("/api/demirbas/list/depo")
def list_demirbas_depo():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT di.id, di.demirbas_code, p.name 
        FROM DemirbasItems di
        JOIN Products p ON di.product_id = p.id
        WHERE di.status = 'DEPO'
        ORDER BY p.name
    """)
    res = cur.fetchall(); conn.close(); return res

@app.post("/api/zimmet/ver")
def zimmet_ver(req: ZimmetVerReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # Ürün Depoda mı?
        cur.execute("SELECT status FROM DemirbasItems WHERE id=%s", (req.demirbas_item_id,))
        if cur.fetchone()['status'] != 'DEPO': raise Exception("Bu ürün depoda değil, zimmetlenemez!")

        # 1. Demirbaş Statüsünü Güncelle
        cur.execute("UPDATE DemirbasItems SET status='ZIMMETLI' WHERE id=%s", (req.demirbas_item_id,))
        
        # 2. Zimmet Kaydı At
        cur.execute("""
            INSERT INTO DemirbasAssignments (demirbas_item_id, personel_id, description)
            VALUES (%s, %s, %s)
        """, (req.demirbas_item_id, req.personel_id, req.description))
        
        conn.commit()
        return {"status": "success", "msg": "Zimmetlendi"}
    except Exception as e: 
        conn.rollback(); raise HTTPException(400, str(e))
    finally: conn.close()

# İade Alınacak Zimmetleri Listele
@app.get("/api/zimmet/active")
def list_active_zimmet():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT da.id as assignment_id, di.demirbas_code, p.name as product_name, per.full_name, da.assigned_at
        FROM DemirbasAssignments da
        JOIN DemirbasItems di ON da.demirbas_item_id = di.id
        JOIN Products p ON di.product_id = p.id
        JOIN Personnel per ON da.personel_id = per.id
        WHERE da.returned_at IS NULL
    """)
    res = cur.fetchall(); conn.close(); return res

@app.post("/api/zimmet/iade")
def zimmet_iade(req: ZimmetIadeReq):
    conn = get_db(); cur = conn.cursor()
    try:
        # 1. Assignment'ı kapat
        cur.execute("""
            UPDATE DemirbasAssignments 
            SET returned_at=CURRENT_TIMESTAMP, return_condition=%s, description=%s
            WHERE id=%s RETURNING demirbas_item_id
        """, (req.condition, req.description, req.assignment_id))
        res = cur.fetchone()
        if not res: raise Exception("Kayıt bulunamadı")
        item_id = res['demirbas_item_id']

        # 2. Demirbaş Statüsünü Güncelle
        new_status = 'HURDA' if req.condition == 'HURDA' else 'DEPO'
        cur.execute("UPDATE DemirbasItems SET status=%s WHERE id=%s", (new_status, item_id))
        
        conn.commit()
        return {"status": "success", "msg": f"İade alındı. Durum: {new_status}"}
    except Exception as e:
        conn.rollback(); raise HTTPException(400, str(e))
    finally: conn.close()

# --- RAPORLAR ---
@app.get("/api/reports/sarf")
def report_sarf():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.name, u.code as unit,
        COALESCE(SUM(CASE WHEN sm.movement_type='IN' THEN sm.quantity ELSE -sm.quantity END), 0) as stock
        FROM Products p
        JOIN MaterialTypes mt ON p.material_type_id = mt.id
        JOIN Units u ON p.unit_id = u.id
        LEFT JOIN StockMovements sm ON p.id = sm.product_id
        WHERE mt.code = 'SARF'
        GROUP BY p.id, p.name, u.code
    """)
    res = cur.fetchall(); conn.close(); return res

@app.get("/api/reports/demirbas")
def report_demirbas():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.name, di.status, COUNT(*) as count
        FROM DemirbasItems di
        JOIN Products p ON di.product_id = p.id
        GROUP BY p.name, di.status
        ORDER BY p.name
    """)
    res = cur.fetchall(); conn.close(); return res

# --- PERSONEL EKLEME ---
@app.post("/api/personel")
def add_personel(req: PersonelEkleReq):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO Personnel (full_name, department_id) VALUES (%s, %s)", (req.full_name, req.department_id))
    conn.commit(); conn.close()
    return {"msg":"OK"}

# --- ARAYÜZ ---
@app.get("/", response_class=HTMLResponse)
def home():
    try: return open("index.html", "r", encoding="utf-8").read()
    except: return "Index.html yüklenemedi."