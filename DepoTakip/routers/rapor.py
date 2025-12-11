from fastapi import APIRouter, Depends
from sqlmodel import Session, select, col, or_
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

# Kendi modüllerimiz
from app.database import get_session
from app.models import (
    Hareket, Urun, Depo, Personel, Bolum, 
    StokSarf, DemirbasVarlik, IslemTipi, UrunTipi, DemirbasDurumu
)

router = APIRouter(
    prefix="/rapor",
    tags=["Raporlama Merkezi"]
)

# --- FİLTRE MODELLERİ (Kullanıcının Seçenekleri) ---

class HareketFiltre(BaseModel):
    baslangic_tarihi: Optional[datetime] = None
    bitis_tarihi: Optional[datetime] = None
    urun_adi: Optional[str] = None # "Eldiven" yazınca tüm eldivenleri bulur
    islem_tipi: Optional[IslemTipi] = None # Sadece ZIMMET_VER olanları getir gibi
    depo_id: Optional[int] = None
    personel_id: Optional[int] = None

class StokFiltre(BaseModel):
    depo_id: Optional[int] = None
    urun_tipi: Optional[UrunTipi] = None # Sadece SARF veya DEMIRBAS
    kritik_stok_altinda: bool = False # Güvenlik stoğunun altına düşenleri göster

# --- ÇIKTI MODELLERİ (Frontend'e Gidecek Temiz Veri) ---
# Veritabanı ID'leri yerine isimleri gönderiyoruz.

class HareketRaporu(BaseModel):
    tarih: datetime
    islem: str
    urun: str
    miktar: float
    kaynak: str # Depo adı veya 'Dışarıdan'
    hedef: str # Personel adı, Bölüm adı veya Depo adı
    aciklama: str

# ----------------------------------------------------------------
# 1. HAREKET GEÇMİŞİ (Timeline) - DETAYLI FİLTRELEME
# ----------------------------------------------------------------
@router.post("/gecmis", response_model=List[HareketRaporu])
def hareket_gecmisi(filtre: HareketFiltre, db: Session = Depends(get_session)):
    """
    Tarih aralığı, ürün ismi, personel veya işlem tipine göre
    geçmişteki tüm olayları filtreleyip getirir.
    """
    # Sorguyu başlat (Join'ler ile isimleri de alacağız)
    query = select(Hareket, Urun, Depo, Personel, Bolum).join(Urun)
    
    # Left Join çünkü bazı alanlar boş olabilir (Personel ID yoksa işlem Depo'yadır vb.)
    query = query.join(Depo, Hareket.cikis_depo_id == Depo.id, isouter=True)
    query = query.join(Personel, Hareket.personel_id == Personel.id, isouter=True)
    query = query.join(Bolum, Hareket.bolum_id == Bolum.id, isouter=True)

    # --- DİNAMİK FİLTRELER ---
    
    # 1. Tarih Aralığı
    if filtre.baslangic_tarihi:
        query = query.where(Hareket.tarih >= filtre.baslangic_tarihi)
    if filtre.bitis_tarihi:
        query = query.where(Hareket.tarih <= filtre.bitis_tarihi)
        
    # 2. Ürün Arama (İçinde geçen kelimeye göre)
    if filtre.urun_adi:
        # Case insensitive (Büyük/küçük harf duyarsız) arama
        query = query.where(col(Urun.ad).ilike(f"%{filtre.urun_adi}%"))
        
    # 3. İşlem Tipi
    if filtre.islem_tipi:
        query = query.where(Hareket.islem_tipi == filtre.islem_tipi)
        
    # 4. Depo ve Personel
    if filtre.depo_id:
        # Hem giriş hem çıkış yapılan işlemde bu depo var mı?
        query = query.where(or_(Hareket.cikis_depo_id == filtre.depo_id, Hareket.giris_depo_id == filtre.depo_id))
        
    if filtre.personel_id:
        query = query.where(Hareket.personel_id == filtre.personel_id)

    # Sonuçları Tarihe Göre Sırala (En yeni en üstte)
    sonuclar = db.exec(query.order_by(Hareket.tarih.desc())).all()
    
    # Veriyi Formatla (Frontend için okunabilir hale getir)
    rapor_listesi = []
    for h, u, d, p, b in sonuclar:
        # Kaynak İsmi Belirleme
        kaynak_isim = "-"
        if h.cikis_depo_id:
            # Kaynak depoyu bulmak için tekrar sorgu gerekebilir veya join mantığını genişletebiliriz.
            # Basitlik adına sorgu anında alınan 'd' değişkeni çıkış deposuysa onu kullanıyoruz.
            kaynak_isim = d.ad if d and d.id == h.cikis_depo_id else "Depo Transfer/Giriş"
        elif h.islem_tipi == IslemTipi.GIRIS:
            kaynak_isim = "Satın Alma / Tedarikçi"
            
        # Hedef İsmi Belirleme
        hedef_isim = "-"
        if h.giris_depo_id:
             # Eğer giriş deposu joinlenen 'd' değilse veritabanından adını çekmek gerekebilir.
             # Ancak hızlı çözüm için:
             hedef_isim = "Depo" 
        if p: hedef_isim = f"Personel: {p.ad_soyad}"
        if b: hedef_isim = f"Bölüm: {b.ad}"

        rapor_listesi.append(HareketRaporu(
            tarih=h.tarih,
            islem=h.islem_tipi,
            urun=u.ad,
            miktar=h.miktar,
            kaynak=kaynak_isim,
            hedef=hedef_isim,
            aciklama=h.aciklama or ""
        ))
        
    return rapor_listesi

# ----------------------------------------------------------------
# 2. ANLIK STOK DURUMU (Depoda ne var?)
# ----------------------------------------------------------------
@router.post("/stok-durumu")
def stok_durumu(filtre: StokFiltre, db: Session = Depends(get_session)):
    """
    Depolardaki sarf malzemelerin güncel durumunu gösterir.
    Kritik stok seviyesinin altındakileri filtreleyebilir.
    """
    query = select(StokSarf, Urun, Depo).join(Urun).join(Depo)
    
    if filtre.depo_id:
        query = query.where(StokSarf.depo_id == filtre.depo_id)
        
    if filtre.urun_tipi:
        query = query.where(Urun.tip == filtre.urun_tipi)

    # Kritik Stok Filtresi
    if filtre.kritik_stok_altinda:
        query = query.where(StokSarf.miktar <= Urun.guvenlik_stogu)
        
    sonuclar = db.exec(query).all()
    
    liste = []
    for s, u, d in sonuclar:
        durum = "NORMAL"
        if s.miktar <= u.guvenlik_stogu:
            durum = "KRİTİK"
            
        liste.append({
            "depo": d.ad,
            "urun": u.ad,
            "sku": u.sku,
            "miktar": s.miktar,
            "birim": u.birim,
            "guvenlik_stogu": u.guvenlik_stogu,
            "durum_analizi": durum
        })
    return liste

# ----------------------------------------------------------------
# 3. ZİMMET RAPORU (Kimde ne var?)
# ----------------------------------------------------------------
@router.get("/zimmet-listesi")
def zimmet_listesi(
    personel_adi: Optional[str] = None, 
    db: Session = Depends(get_session)
):
    """
    Şu an sahada (zimmette) olan tüm demirbaşları listeler.
    İsimle arama yapılabilir.
    """
    query = select(DemirbasVarlik, Urun, Personel, Bolum)\
        .join(Urun)\
        .join(Personel, isouter=True)\
        .join(Bolum, isouter=True)\
        .where(DemirbasVarlik.durum == DemirbasDurumu.ZIMMETLI)
    
    if personel_adi:
        query = query.where(col(Personel.ad_soyad).ilike(f"%{personel_adi}%"))
        
    sonuclar = db.exec(query).all()
    
    liste = []
    for d, u, p, b in sonuclar:
        sahip = p.ad_soyad if p else (b.ad if b else "Bilinmiyor")
        liste.append({
            "demirbas_no": d.ozel_kod,
            "urun": u.ad,
            "zimmetli_kisi_birim": sahip,
            "seri_no": d.seri_no
        })
    return liste