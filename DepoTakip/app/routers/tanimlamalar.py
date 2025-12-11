from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List

# Kendi modüllerimiz
from app.database import get_session
from app.models import Depo, Bolum, Personel, Urun, UrunTipi

# Router Tanımı
router = APIRouter(
    prefix="/tanim",
    tags=["Tanımlamalar (Depo, Ürün, Personel)"]
)

# ----------------------------------------------------------------
# 1. DEPO İŞLEMLERİ
# ----------------------------------------------------------------

@router.post("/depo", response_model=Depo)
def depo_ekle(depo: Depo, db: Session = Depends(get_session)):
    """Yeni bir depo oluşturur. Aynı isimde varsa hata verir."""
    # Aynı isimde depo var mı kontrol et
    mevcut = db.exec(select(Depo).where(Depo.ad == depo.ad)).first()
    if mevcut:
        raise HTTPException(status_code=400, detail="Bu isimde bir depo zaten var.")
    
    db.add(depo)
    db.commit()
    db.refresh(depo)
    return depo

@router.get("/depo", response_model=List[Depo])
def depo_listele(aktif_sadece: bool = True, db: Session = Depends(get_session)):
    """Depoları listeler. Varsayılan olarak sadece aktifleri getirir."""
    if aktif_sadece:
        return db.exec(select(Depo).where(Depo.aktif_mi == True)).all()
    return db.exec(select(Depo)).all()

@router.put("/depo/{id}/pasif")
def depo_pasife_al(id: int, db: Session = Depends(get_session)):
    """Depoyu silmez, pasife alır."""
    depo = db.get(Depo, id)
    if not depo:
        raise HTTPException(status_code=404, detail="Depo bulunamadı")
    
    depo.aktif_mi = False
    db.add(depo)
    db.commit()
    return {"mesaj": f"{depo.ad} pasife alındı."}

# ----------------------------------------------------------------
# 2. BÖLÜM İŞLEMLERİ
# ----------------------------------------------------------------

@router.post("/bolum", response_model=Bolum)
def bolum_ekle(bolum: Bolum, db: Session = Depends(get_session)):
    mevcut = db.exec(select(Bolum).where(Bolum.ad == bolum.ad)).first()
    if mevcut:
        raise HTTPException(status_code=400, detail="Bu isimde bir bölüm zaten var.")
    
    db.add(bolum)
    db.commit()
    db.refresh(bolum)
    return bolum

@router.get("/bolum", response_model=List[Bolum])
def bolum_listele(aktif_sadece: bool = True, db: Session = Depends(get_session)):
    if aktif_sadece:
        return db.exec(select(Bolum).where(Bolum.aktif_mi == True)).all()
    return db.exec(select(Bolum)).all()

@router.put("/bolum/{id}/pasif")
def bolum_pasife_al(id: int, db: Session = Depends(get_session)):
    bolum = db.get(Bolum, id)
    if not bolum:
        raise HTTPException(status_code=404, detail="Bölüm bulunamadı")
    
    bolum.aktif_mi = False
    db.add(bolum)
    db.commit()
    return {"mesaj": f"{bolum.ad} pasife alındı."}

# ----------------------------------------------------------------
# 3. PERSONEL İŞLEMLERİ
# ----------------------------------------------------------------

@router.post("/personel", response_model=Personel)
def personel_ekle(personel: Personel, db: Session = Depends(get_session)):
    # Bölüm kontrolü
    if personel.bolum_id:
        bolum = db.get(Bolum, personel.bolum_id)
        if not bolum:
            raise HTTPException(status_code=404, detail="Seçilen bölüm bulunamadı.")
            
    db.add(personel)
    db.commit()
    db.refresh(personel)
    return personel

@router.get("/personel", response_model=List[Personel])
def personel_listele(aktif_sadece: bool = True, db: Session = Depends(get_session)):
    if aktif_sadece:
        return db.exec(select(Personel).where(Personel.aktif_mi == True)).all()
    return db.exec(select(Personel)).all()

@router.put("/personel/{id}/pasif")
def personel_pasife_al(id: int, db: Session = Depends(get_session)):
    per = db.get(Personel, id)
    if not per:
        raise HTTPException(status_code=404, detail="Personel bulunamadı")
    
    per.aktif_mi = False
    db.add(per)
    db.commit()
    return {"mesaj": f"{per.ad_soyad} pasife alındı."}

# ----------------------------------------------------------------
# 4. ÜRÜN İŞLEMLERİ (ÖNEMLİ)
# ----------------------------------------------------------------

@router.post("/urun", response_model=Urun)
def urun_ekle(urun: Urun, db: Session = Depends(get_session)):
    """
    Ürün eklerken SKU (Stok Kodu) benzersiz olmalıdır.
    """
    # SKU kontrolü
    mevcut = db.exec(select(Urun).where(Urun.sku == urun.sku)).first()
    if mevcut:
        raise HTTPException(status_code=400, detail="Bu SKU koduna sahip bir ürün zaten var!")
    
    # Demirbaş Mantık Kontrolü (Opsiyonel ama iyi bir pratik)
    # Demirbaş ise birim genelde 'ADET' olur, ama kullanıcıya bırakıyoruz.
    
    db.add(urun)
    db.commit()
    db.refresh(urun)
    return urun

@router.get("/urun", response_model=List[Urun])
def urun_listele(tip: UrunTipi = None, aktif_sadece: bool = True, db: Session = Depends(get_session)):
    """
    İsteğe bağlı olarak 'SARF' veya 'DEMIRBAS' filtresi yapılabilir.
    """
    query = select(Urun)
    
    if aktif_sadece:
        query = query.where(Urun.aktif_mi == True)
    
    if tip:
        query = query.where(Urun.tip == tip)
        
    return db.exec(query).all()

@router.put("/urun/{id}/pasif")
def urun_pasife_al(id: int, db: Session = Depends(get_session)):
    urun = db.get(Urun, id)
    if not urun:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    
    urun.aktif_mi = False
    db.add(urun)
    db.commit()
    return {"mesaj": f"{urun.ad} ({urun.sku}) pasife alındı."}