import sqlite3

def veritabani_olustur():
    # Veritabanı dosyasına bağlan (yoksa otomatik oluşturur)
    baglanti = sqlite3.connect('depo.db')
    imlec = baglanti.cursor()

    # 1. Urunler Tablosu
    # SQLite'da SERIAL yerine INTEGER PRIMARY KEY AUTOINCREMENT kullanılır.
    imlec.execute('''
        CREATE TABLE IF NOT EXISTS Urunler (
            urun_id INTEGER PRIMARY KEY AUTOINCREMENT,
            urun_adi TEXT NOT NULL,
            SKU TEXT UNIQUE NOT NULL,
            birim TEXT NOT NULL,
            guvenlik_stogu INTEGER DEFAULT 0
        )
    ''')

    # 2. StokGiris Tablosu
    imlec.execute('''
        CREATE TABLE IF NOT EXISTS StokGiris (
            giris_id INTEGER PRIMARY KEY AUTOINCREMENT,
            urun_id INTEGER NOT NULL,
            miktar REAL NOT NULL,
            giris_tarihi TEXT NOT NULL,
            belge_no TEXT,
            FOREIGN KEY (urun_id) REFERENCES Urunler (urun_id)
        )
    ''')

    # 3. StokCikis Tablosu
    imlec.execute('''
        CREATE TABLE IF NOT EXISTS StokCikis (
            cikis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            urun_id INTEGER NOT NULL,
            miktar REAL NOT NULL,
            cikis_tarihi TEXT NOT NULL,
            sevkiyat_no TEXT,
            FOREIGN KEY (urun_id) REFERENCES Urunler (urun_id)
        )
    ''')

    baglanti.commit()
    baglanti.close()
    print("Harika! 'depo.db' veritabanı ve tablolar başarıyla oluşturuldu.")

if __name__ == "__main__":
    veritabani_olustur()