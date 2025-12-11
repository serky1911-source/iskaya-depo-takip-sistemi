from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional, List

# Kendi modüllerimiz
from app.database import get_session
from app.models import (
    DemirbasVarlik, Personel, Bolum, Depo, Hareket, 
    DemirbasDurumu, IslemTipi
)

router = APIRouter(
    prefix="/demirbas",
    tags=["Demirbaş & Zimmet Yönetimi"]
)

# --- İSTEK MODELLERİ ---

class ZimmetVerModel(BaseModel):
    demirbas_id: int
    personel_id: Optional[int] = None
    bolum_id: Optional[int] = None
    aciklama: str = ""

class ZimmetIadeModel(BaseModel):
    demirbas_id: int
    hedef_depo_id: int # İade nereye bırakıldı?
    durum: DemirbasDurumu # SAGLAM, ARIZALI veya HURDA
    aciklama: str = ""

# ----------------------------------------------------------------
# 1. ZİMMET VERME (Personel veya Bölüme)
# ----------------------------------------------------------------
@router.post("/zimmetle")
def zimmet_ver(veri: ZimmetVerModel, db: Session = Depends(get_session)):
    """
    Seçilen demirbaşı depodan alır, personelin/bölümün üzerine kaydeder.
    """
    # 1. Demirbaşı Bul
    demirbas = db.get(DemirbasVarlik, veri.demirbas_id)
    if not demirbas:
        raise HTTPException(status_code=404, detail="Demirbaş bulunamadı.")
    
    # 2. Müsaitlik Kontrolü (Manifesto Kuralı)
    if demirbas.durum != DemirbasDurumu.DEPODA:
        raise HTTPException(
            status_code=400, 
            detail=f"Bu ürün zimmetlenemez! Şu anki durumu: {demirbas.durum}. Sadece DEPODA olanlar verilebilir."
        )

    # 3. Hedef Kontrolü (Kime veriyoruz?)
    hedef_isim = ""
    if veri.personel_id:
        personel = db.get(Personel, veri.personel_id)
        if not personel or not personel.aktif_mi:
            raise HTTPException(status_code=400, detail="Personel bulunamadı veya pasif.")
        hedef_isim = f"Personel: {personel.ad_soyad}"
        
        # Atama
        demirbas.zimmetli_personel_id = veri.personel_id
        demirbas.zimmetli_bolum_id = None # Temizle
        
    elif veri.bolum_id:
        bolum = db.get(Bolum, veri.bolum_id)
        if not bolum or not bolum.aktif_mi:
            raise HTTPException(status_code=400, detail="Bölüm bulunamadı veya pasif.")
        hedef_isim = f"Bölüm: {bolum.ad}"
        
        # Atama
        demirbas.zimmetli_bolum_id = veri.bolum_id
        demirbas.zimmetli_personel_id = None # Temizle
        
    else:
        raise HTTPException(status_code=400, detail="Zimmet için Personel ID veya Bölüm ID seçilmelidir.")

    # 4. Durum Güncelleme
    eski_depo_id = demirbas.bulundugu_depo_id
    demirbas.durum = DemirbasDurumu.ZIMMETLI
    demirbas.bulundugu_depo_id = None # Artık depoda değil, sahada.

    # 5. Loglama
    log = Hareket(
        islem_tipi=IslemTipi.ZIMMET_VER,
        urun_id=demirbas.urun_id,
        demirbas_id=demirbas.id,
        cikis_depo_id=eski_depo_id,
        personel_id=veri.personel_id,
        bolum_id=veri.bolum_id,
        miktar=1,
        aciklama=f"{hedef_isim} - {veri.aciklama}"
    )
    
    db.add(log)
    db.add(demirbas)
    db.commit()
    return {"mesaj": f"Demirbaş ({demirbas.ozel_kod}) başarıyla zimmetlendi."}

# ----------------------------------------------------------------
# 2. ZİMMET İADE ALMA
# ----------------------------------------------------------------
@router.post("/iade")
def zimmet_iade(veri: ZimmetIadeModel, db: Session = Depends(get_session)):
    """
    Sahadaki demirbaşı depoya geri alır. Durumu (Sağlam/Arızalı) burada belirlenir.
    """
    demirbas = db.get(DemirbasVarlik, veri.demirbas_id)
    if not demirbas:
        raise HTTPException(status_code=404, detail="Demirbaş bulunamadı.")
    
    # Sadece zimmettekiler iade alınabilir
    if demirbas.durum != DemirbasDurumu.ZIMMETLI:
        raise HTTPException(status_code=400, detail="Bu ürün şu an zimmette görünmüyor.")

    depo = db.get(Depo, veri.hedef_depo_id)
    if not depo:
        raise HTTPException(status_code=404, detail="Hedef depo bulunamadı.")

    # Kimden geldiğini loglamak için saklayalım
    eski_personel = demirbas.zimmetli_personel_id
    eski_bolum = demirbas.zimmetli_bolum_id

    # Durum Güncelleme
    # İade edilen ürün seçilen duruma geçer (SAGLAM, ARIZALI, HURDA)
    demirbas.durum = veri.durum 
    demirbas.bulundugu_depo_id = veri.hedef_depo_id # Fiziksel olarak depoya girdi
    
    # Zimmet kayıtlarını temizle
    demirbas.zimmetli_personel_id = None
    demirbas.zimmetli_bolum_id = None

    # Loglama
    log = Hareket(
        islem_tipi=IslemTipi.ZIMMET_IADE,
        urun_id=demirbas.urun_id,
        demirbas_id=demirbas.id,
        giris_depo_id=veri.hedef_depo_id,
        personel_id=eski_personel,
        bolum_id=eski_bolum,
        miktar=1,
        aciklama=f"İade Durumu: {veri.durum} - {veri.aciklama}"
    )

    db.add(log)
    db.add(demirbas)
    db.commit()
    return {"mesaj": f"Demirbaş iade alındı. Yeni Durum: {veri.durum}"}

# ----------------------------------------------------------------
# 3. LİSTELEME (Sorgulama)
# ----------------------------------------------------------------

@router.get("/list", response_model=List[DemirbasVarlik])
def demirbas_listele(
    durum: Optional[DemirbasDurumu] = None, 
    personel_id: Optional[int] = None,
    db: Session = Depends(get_session)
):
    """
    Demirbaşları filtreleyerek listeler.
    Örn: Sadece ZIMMETLI olanlar veya sadece bir personele ait olanlar.
    """
    query = select(DemirbasVarlik)
    
    if durum:
        query = query.where(DemirbasVarlik.durum == durum)
    
    if personel_id:
        query = query.where(DemirbasVarlik.zimmetli_personel_id == personel_id)
        
    return db.exec(query).all()