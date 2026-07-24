"""
Pengkategorian pengumuman IDX berdasarkan judul/perihal (gratis, tanpa AI).

4 kategori:
- aksi    : Aksi Korporasi (RUPS, dividen, rights issue, obligasi, akuisisi, dll)
- lap     : Laporan & Perjanjian (laporan keuangan/tahunan, perjanjian, transaksi material)
- spam    : rutin/low-value yang SUDAH pasti (Pencatatan Saham, Kepemilikan Saham, dll)
- notsure : tidak cocok ketiganya -> mungkin penting, perlu ditinjau manual

Urutan cek: aksi -> lap -> spam -> notsure (catch-all yang "aman").
"""

KW_AKSI = [
    "rups", "rapat umum pemegang saham", "dividen", "stock split", "pemecahan saham",
    "reverse stock", "rights issue", "hmetd", "penambahan modal", "merger", "penggabungan",
    "akuisisi", "pengambilalihan", "tender", "buyback", "pembelian kembali saham",
    "penawaran umum", "obligasi", "sukuk", "medium term notes", " mtn ",
    "penawaran tender", "kuasi reorganisasi", "delisting", "relisting", "go private",
    "spin off", "spin-off", "private placement",
]

KW_LAP = [
    "laporan keuangan", "laporan tahunan", "perjanjian", "penandatanganan",
    "transaksi material", "transaksi afiliasi", "transaksi benturan", "penggunaan dana",
    "realisasi", "keterbukaan informasi", "kerja sama", "kerjasama", "prospektus",
    "laporan hasil", "nota kesepahaman", "mou",
]

KW_SPAM = [
    "pencatatan saham", "pencatatan tambahan", "pencatatan awal", "pencatatan efek",
    "pencatatan perdana", "pengumuman bursa pencatatan", "laporan kepemilikan",
    "perubahan kepemilikan", "laporan bulanan", "registrasi pemegang efek",
    "penambahan saham", "konversi", "waran", "esop", "msop", "pelaksanaan waran",
]


def classify(judul, perihal=""):
    t = f"{judul or ''} {perihal or ''}".lower()
    if any(k in t for k in KW_AKSI):
        return "aksi"
    if any(k in t for k in KW_LAP):
        return "lap"
    if any(k in t for k in KW_SPAM):
        return "spam"
    return "notsure"
