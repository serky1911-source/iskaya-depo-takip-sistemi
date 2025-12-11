from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship, Enum
import enum

# --- ENUM TİPLERİ (Sistemin Kırmızı Çizgileri) ---

class UrunTipi(str, enum.Enum):
    SARF = "SARF"           # Adet/Miktar ile takip edilir, tükenir.
    DEMIRBAS = "DEMIRBAS"   # Tekil takip edilir, zimmetlenir, tükenmez.

class DemirbasDurumu(str, enum.Enum):
    DEPODA = "DEPODA"       # Depo rafında bekliyor
    ZIMMETLI = "ZIMMETLI"   # Bir personel veya bölümde kullanımda
    ARIZALI = "ARIZALI"     # Tamir bekliyor veya bozuk
    HURDA = "HURDA"         # Kullanım dışı (Çöp)

class IslemTipi(str, enum.Enum):
    GIRIS = "GIRIS"                 # Satın alma / İlk giriş
    CIKIS = "CIKIS"                 # Sarf tüketim çıkışı
    TRANSFER = "TRANSFER"           # Depolar arası transfer
    ZIMMET_VER = "ZIMMET_VER"       # Demirbaşın personele/bölüme verilmesi
    ZIMMET_IADE = "ZIMMET_IADE"     # Demirbaşın depoya geri dönmesi
    DURUM_DEGISTIR = "DURUM_DEGISTIR" # Sağlam -> Arızalı vb.

# --- TEMEL VARLIKLAR ---

class Depo(SQLModel, table=True):
    __tablename__ = "depolar"
    id: Optional[int] = Field(default=None, primary_key=True)
    ad: str = Field(unique=True, index=True)
    aciklama: Optional[str] = None
    aktif_mi: bool = Field(default=True) # Silme yok, pasife alma var.
    olusturma_tarihi: datetime = Field(default_factory=datetime.now)

class Bolum(SQLModel, table=True):
    __tablename__ = "bolumler"
    id: Optional[int] = Field(default=None, primary_key=True)
    ad: str = Field(unique=True, index=True) # Örn: Kaynak Atölyesi
    aktif_mi: bool = Field(default=True)

class Personel(SQLModel, table=True):
    __tablename__ = "personel"
    id: Optional[int] = Field(default=None, primary_key=True)
    ad_soyad: str = Field(index=True)
    bolum_id: Optional[int] = Field(default=None, foreign_key="bolumler.id")
    aktif_mi: bool = Field(default=True) # Pasif personel zimmet alamaz.

class Urun(SQLModel, table=True):
    __tablename__ = "urunler"
    id: Optional[int] = Field(default=None, primary_key=True)
    ad: str = Field(index=True)
    sku: str = Field(unique=True, index=True) # Barkod / Stok Kodu
    tip: UrunTipi = Field(index=True) # SARF veya DEMIRBAS
    birim: str # Adet, Kg, Metre, Koli
    guvenlik_stogu: int = Field(default=0) # Kritik seviye uyarısı için
    aktif_mi: bool = Field(default=True)

# --- STOK YÖNETİMİ (SARF) ---

class StokSarf(SQLModel, table=True):
    """
    Sarf malzemelerin hangi depoda ne kadar olduğunu tutar.
    Demirbaşlar bu tabloda ASLA yer almaz.
    """
    __tablename__ = "stok_sarf"
    id: Optional[int] = Field(default=None, primary_key=True)
    depo_id: int = Field(foreign_key="depolar.id", index=True)
    urun_id: int = Field(foreign_key="urunler.id", index=True)
    miktar: float = Field(default=0) # Negatif olamaz (Logic ile korunacak)

# --- VARLIK YÖNETİMİ (DEMİRBAŞ) ---

class DemirbasVarlik(SQLModel, table=True):
    """
    Her bir satır, fiziksel bir demirbaşı temsil eder.
    10 tane Laptop aldıysak, burada 10 ayrı satır (ID) oluşur.
    """
    __tablename__ = "demirbas_varliklar"
    id: Optional[int] = Field(default=None, primary_key=True)
    urun_id: int = Field(foreign_key="urunler.id")
    seri_no: Optional[str] = Field(default=None) # Cihaz üzerindeki seri no
    ozel_kod: str = Field(unique=True, index=True) # Şirketin verdiği demirbaş no (QR Kodu)
    
    durum: DemirbasDurumu = Field(default=DemirbasDurumu.DEPODA)
    
    # Lokasyon Bilgisi (Sadece biri dolu olabilir mantığı kod tarafında yönetilecek)
    bulundugu_depo_id: Optional[int] = Field(default=None, foreign_key="depolar.id")
    zimmetli_personel_id: Optional[int] = Field(default=None, foreign_key="personel.id")
    zimmetli_bolum_id: Optional[int] = Field(default=None, foreign_key="bolumler.id")

# --- HAREKET LOGLARI (LOGGING) ---

class Hareket(SQLModel, table=True):
    """
    Sistemdeki her işlemin izi burada tutulur. Asla silinmez.
    """
    __tablename__ = "hareketler"
    id: Optional[int] = Field(default=None, primary_key=True)
    tarih: datetime = Field(default_factory=datetime.now, index=True)
    islem_tipi: IslemTipi
    
    urun_id: int = Field(foreign_key="urunler.id")
    
    # Kaynak ve Hedef (Nereden Nereye?)
    cikis_depo_id: Optional[int] = Field(default=None, foreign_key="depolar.id")
    giris_depo_id: Optional[int] = Field(default=None, foreign_key="depolar.id")
    
    # İlgili Kişi/Bölüm (Zimmet için)
    personel_id: Optional[int] = Field(default=None, foreign_key="personel.id")
    bolum_id: Optional[int] = Field(default=None, foreign_key="bolumler.id")
    
    # Detaylar
    miktar: float = Field(default=1) # Sarf için miktar, Demirbaş için genelde 1
    demirbas_id: Optional[int] = Field(default=None, foreign_key="demirbas_varliklar.id")
    
    aciklama: Optional[str] = None
    kullanici: str = Field(default="Sistem") # İşlemi yapan admin/kullanıcı adı