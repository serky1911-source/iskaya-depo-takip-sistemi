from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

# Kendi modüllerimiz
from app.database import get_session
from app.models import (
    Urun, Depo, Bolum, StokSarf, DemirbasVarlik, Hareket, 
    IslemTipi, UrunTipi, DemirbasDurumu
)

router = APIRouter(
    prefix="/islem",
    tags=["Stok İşlemleri (Giriş/Çıkış/Transfer)"]
)

# --- GİRİŞ MODELLERİ (Veri Doğrulama İçin) ---
# Kullanıcının göndereceği veriyi kontrol eden şablonlar

class StokGirisModel(BaseModel):
    urun_id: int
    depo_id: int
    miktar: float
    aciklama: str = ""

class StokTransferModel(BaseModel):
    urun_id: int
    cikis_depo_id: int
    giris_depo_id: int
    miktar: float
    aciklama: str = ""

class StokCikisModel(BaseModel):
    urun_id: int
    depo_id: int
    bolum_id: int # Hangi birime çıkılıyor?
    miktar: float
    aciklama: str = ""

# ----------------------------------------------------------------
# 1. MAL KABUL (GİRİŞ)
# ----------------------------------------------------------------
@router.post("/giris")
def stok_giris(veri: StokGirisModel, db: Session = Depends(get_session)):
    """
    Depoya ürün girişi yapar.
    - Eğer SARF ise: Depodaki miktarı artırır.
    - Eğer DEMİRBAŞ ise: Girilen miktar kadar 'Tekil Varlık' oluşturur.
    """
    # 1. Ürünü Bul
    urun = db.get(Urun, veri.urun_id)
    if not urun:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    
    # 2. Depoyu Bul
    depo = db.get(Depo, veri.depo_id)
    if not depo or not depo.aktif_mi:
        raise HTTPException(status_code=400, detail="Depo bulunamadı veya pasif.")

    # --- SENARYO A: SARF MALZEME GİRİŞİ ---
    if urun.tip == UrunTipi.SARF:
        # Stok kaydı var mı bak, yoksa 0 ile oluştur.
        stok = db.exec(
            select(StokSarf)
            .where(StokSarf.depo_id == veri.depo_id)
            .where(StokSarf.urun_id == veri.urun_id)
        ).first()
        
        if not stok:
            stok = StokSarf(depo_id=veri.depo_id, urun_id=veri.urun_id, miktar=0)
            db.add(stok)
        
        # Miktarı artır
        stok.miktar += veri.miktar
        
        # Hareket Logu Oluştur
        log = Hareket(
            islem_tipi=IslemTipi.GIRIS,
            urun_id=veri.urun_id,
            giris_depo_id=veri.depo_id,
            miktar=veri.miktar,
            aciklama=veri.aciklama
        )
        db.add(log)
        db.commit()
        return {"mesaj": f"{urun.ad} ({veri.miktar} {urun.birim}) depoya eklendi."}

    # --- SENARYO B: DEMİRBAŞ GİRİŞİ (KRİTİK) ---
    elif urun.tip == UrunTipi.DEMIRBAS:
        # Demirbaş adedi tam sayı olmalı
        adet = int(veri.miktar)
        if adet <= 0:
            raise HTTPException(status_code=400, detail="Demirbaş adedi en az 1 olmalıdır.")
        
        yaratilan_kodlar = []
        
        # Adet kadar döngü kurup tek tek varlık yaratıyoruz
        for i in range(adet):
            # Benzersiz Kod Üretimi (Örn: DEM-20231025-X8Y1)
            ozel_kod = f"DEM-{uuid.uuid4().hex[:8].upper()}"
            
            yeni_demirbas = DemirbasVarlik(
                urun_id=urun.id,
                ozel_kod=ozel_kod,
                durum=DemirbasDurumu.DEPODA,
                bulundugu_depo_id=veri.depo_id
            )
            db.add(yeni_demirbas)
            db.commit() # ID oluşması için commit gerek
            db.refresh(yeni_demirbas)
            
            # Her bir demirbaş için ayrı giriş logu (İzlenebilirlik için şart)
            log = Hareket(
                islem_tipi=IslemTipi.GIRIS,
                urun_id=urun.id,
                giris_depo_id=veri.depo_id,
                miktar=1, # Demirbaş her zaman 1
                demirbas_id=yeni_demirbas.id,
                aciklama=f"Toplu Giriş - {veri.aciklama}"
            )
            db.add(log)
            yaratilan_kodlar.append(ozel_kod)
            
        db.commit()
        return {
            "mesaj": f"{adet} adet demirbaş tekil olarak sisteme işlendi.",
            "kodlar": yaratilan_kodlar
        }

# ----------------------------------------------------------------
# 2. SARF TRANSFER (Depo -> Depo)
# ----------------------------------------------------------------
@router.post("/transfer")
def stok_transfer(veri: StokTransferModel, db: Session = Depends(get_session)):
    """
    Sadece SARF malzemeler için depolar arası transfer.
    Demirbaşlar 'Zimmet' ile yer değiştirir veya 'Demirbaş Transfer' modülü gerekir.
    Burada sadece Sarf'a izin veriyoruz (Manifesto gereği).
    """
    urun = db.get(Urun, veri.urun_id)
    if urun.tip != UrunTipi.SARF:
        raise HTTPException(status_code=400, detail="Demirbaşlar bu menüden transfer edilemez! Zimmet veya Demirbaş Atama kullanın.")

    # Kaynak Depo Stok Kontrolü
    kaynak_stok = db.exec(
        select(StokSarf)
        .where(StokSarf.depo_id == veri.cikis_depo_id)
        .where(StokSarf.urun_id == veri.urun_id)
    ).first()

    if not kaynak_stok or kaynak_stok.miktar < veri.miktar:
        mevcut = kaynak_stok.miktar if kaynak_stok else 0
        raise HTTPException(status_code=400, detail=f"Yetersiz Stok! Kaynak depoda mevcut: {mevcut}")

    # Hedef Depo Stok Bul/Oluştur
    hedef_stok = db.exec(
        select(StokSarf)
        .where(StokSarf.depo_id == veri.giris_depo_id)
        .where(StokSarf.urun_id == veri.urun_id)
    ).first()

    if not hedef_stok:
        hedef_stok = StokSarf(depo_id=veri.giris_depo_id, urun_id=veri.urun_id, miktar=0)
        db.add(hedef_stok)

    # Transfer İşlemi
    kaynak_stok.miktar -= veri.miktar
    hedef_stok.miktar += veri.miktar

    # Loglama
    log = Hareket(
        islem_tipi=IslemTipi.TRANSFER,
        urun_id=veri.urun_id,
        cikis_depo_id=veri.cikis_depo_id,
        giris_depo_id=veri.giris_depo_id,
        miktar=veri.miktar,
        aciklama=veri.aciklama
    )
    db.add(log)
    db.commit()
    return {"mesaj": "Transfer başarıyla tamamlandı."}

# ----------------------------------------------------------------
# 3. SARF ÇIKIŞ (Tüketim)
# ----------------------------------------------------------------
@router.post("/cikis")
def stok_cikis(veri: StokCikisModel, db: Session = Depends(get_session)):
    """
    Depodan bir bölüme sarf malzeme çıkışı (Tüketim).
    Stoktan düşer. Geri dönüşü yoktur (İade hariç).
    """
    urun = db.get(Urun, veri.urun_id)
    if urun.tip != UrunTipi.SARF:
        raise HTTPException(status_code=400, detail="Demirbaşlar 'Çıkış' yapılamaz, Zimmetlenmelidir!")

    # Stok Kontrolü
    stok = db.exec(
        select(StokSarf)
        .where(StokSarf.depo_id == veri.depo_id)
        .where(StokSarf.urun_id == veri.urun_id)
    ).first()

    if not stok or stok.miktar < veri.miktar:
        raise HTTPException(status_code=400, detail="Yetersiz Stok!")

    # Bölüm Kontrolü
    bolum = db.get(Bolum, veri.bolum_id)
    if not bolum:
        raise HTTPException(status_code=404, detail="Hedef bölüm bulunamadı.")

    # İşlem
    stok.miktar -= veri.miktar
    
    # Loglama
    log = Hareket(
        islem_tipi=IslemTipi.CIKIS,
        urun_id=veri.urun_id,
        cikis_depo_id=veri.depo_id,
        bolum_id=veri.bolum_id, # Hangi bölüme gitti?
        miktar=veri.miktar,
        aciklama=veri.aciklama
    )
    db.add(log)
    db.commit()
    return {"mesaj": "Çıkış işlemi onaylandı."}