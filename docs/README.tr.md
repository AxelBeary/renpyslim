# RenPySlim

> Ren'Py kaynak dosyalarınızı tek elden inceltip paketleyen hepsi bir arada araç kutusu

**Dil / Language:** [简体中文（默认）](../README.md) | [English](README.en.md) | [Русский](README.ru.md) | [Español](README.es.md) | [Português (BR)](README.pt.md) | **Türkçe** | [Deutsch](README.de.md) | [Français](README.fr.md)

**Lisans: [AGPL-3.0](../LICENSE)** · Üçüncü taraf bildirimleri için [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

> Bu araç yapay zekâyla yoğun biçimde geliştirilmiştir; kullanmadan önce kodu gözden geçirmenizi öneririz. Geliştirici, yanlış kullanımdan doğan hiçbir sonuçtan sorumlu değildir. **Verileriniz değerlidir!**

---

## Bu nedir

RenPySlim, Ren'Py oyun geliştiricilerinin oyunlarını **küçültmesine, düzenlemesine ve paketleyip yayınlamasına** yardımcı olur — hepsi tek bir akışta:

- **Analiz** — şişkin dosyaları tarar; boyut / sorun / öneri raporu sunar
- **Sıkıştırma** — görselleri, sesleri, videoları ve yazı tiplerini inceltir,
  senaryo (script) referanslarını otomatik olarak yeniden yazar;
  varsayılan ayar kalite önceliklidir (q95, kayıpsıza yakın) ve paralel optimizasyon tüm çekirdeklerden otomatik yararlanır
- **Paketleme** — resmî SDK'yı kullanarak PC / Mac / Android sürümleri oluşturur
- **Hazır ürünü inceltme** — zaten paketlenmiş bir oyunu (klasör ya da zip/7z/rar) güvenle inceltir; olduğu gibi alır, olduğu gibi teslim eder
- **APK inceltme** — Android paketleri de inceltilir: görseller WebP'ye, sesler OGG'ye dönüştürülür (çalışma anında yeniden eşleme yapılır, referanslara dokunulmaz), otomatik olarak yeniden imzalanır
- **Derleme çözme kilidi** (deneysel) — kaynak kodu olmayan hazır ürünlerde, gömülü unrpyc ile kaynak kodu geri kazanılır;
  paket içindeki görseller/sesler de dönüştürülebilir ve dönüştürme sonrası her şey olduğu gibi RPA paketine geri konur

Yanında proje sağlık kontrolü dörtlüsü: atıl dosya tespiti, paketleme öncesi
gereksiz dosya temizliği, kopya dosya tespiti ve yazı tipi eksik karakter
raporu; her optimizasyondan sonra resmî lint denetimi otomatik çalıştırılır.

**Varsayılan olarak güvenli**: tüm işlemler önce bir çalışma kopyasına yapılır,
asıl dosyalara dokunulmaz; "küçülmediyse değiştirilmez"; referansı bulunamayan
dosyaların adı asla değiştirilmez; her çalıştırmada analiz raporu ve değişiklik
listesi oluşturulur.

## Hızlı başlangıç

**Normal kullanıcılar**: [Releases](https://github.com/AxelBeary/renpyslim/releases)
sayfasından `RenPySlim.exe` dosyasını indirin, çift tıklayıp çalıştırın;
tarayıcınız otomatik olarak yönetim sayfasını açar.

**Geliştiriciler**:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py            # 启动图形界面
```

## Grafik arayüz (önerilir)

Arayüz kenar çubuğu düzenindedir; **中文 / English / Русский / Español / Português (BR) / Türkçe / Deutsch / Français** dillerini ve
**açık/koyu temayı** destekler (sağ üst köşeden geçiş yapılır; elle seçim
yapılmazsa tarayıcı diline ve sistem görünümüne uyar, seçiminiz hatırlanır).
Dört giriş noktası: **Süper Paketleyici / Hazır Ürün İnceltme / APK İnceltme / Yazı Tipi İnceltme**.

### Dört adımlı ana akış rehberi

1. Yolu girin (veya "Arşiv seç / Klasör seç" düğmesiyle seçim penceresini açın), "Tara ve analiz et"e tıklayın → analiz raporunu görün
2. Yapmak istediğiniz optimizasyonları işaretleyin, sıkıştırma ayarını seçin
3. "Çalıştır"a tıklayın → ilerleme çubuğunu ve günlükleri canlı izleyin
4. İşlem bitince optimizasyon sonucunu / resmî yayın paketlerini alın

### Kolaylaştırıcı işlemler

- zip / 7z / rar / APK / klasörü **doğrudan araç simgesinin üzerine sürükleyin**; yol otomatik doldurulur ve ilgili özelliğe geçilir
- Araç zaten çalışırken yeni bir dosya sürüklerseniz yeni bir sekmede açılır, uygulama tekrar başlatılmaz
- Kullanılmış yollar "Son kullanılanlar"da saklanır, tek tıkla yeniden kullanılabilir

### Dört özellik giriş noktası

- **Süper Paketleyici**: proje klasörünü gösterin; optimizasyon sonrası resmî SDK otomatik olarak paketler (PC/Mac/Android);
  "paketleme sırasında kaynakları RPA paketine koy" seçeneği işaretlenebilir (resmî kanal)
- **Hazır Ürün İnceltme**: hazır ürün klasörünü gösterin ya da doğrudan zip / 7z / rar arşivi bırakın
  (otomatik açılır, inceltme sonrası otomatik yeniden paketlenip teslim edilir; şifreli arşivler desteklenir);
  RPA paketleri otomatik olarak açılıp inceltildikten sonra yeniden oluşturulur;
  arşivin içinden APK çıkarsa güvenli APK inceltme akışına otomatik geçilir;
  gelişmiş seçeneklerdeki deneysel "derleme çözerek biçim dönüştürme kilidi" anahtarı,
  kaynak kodu olmayan ürünlerin de biçim dönüştürme avantajından yararlanmasını sağlar
- **APK İnceltme**: bir .apk dosyası seçin, üç adımda biter (sıkıştırma ayarı / tam inceltme anahtarı / üç imza seçeneğinden biri;
  varsayılan olarak otomatik yeni anahtar üretilir); doğrudan kurulabilir inceltilmiş paket teslim edilir
- **Yazı Tipi İnceltme** (bağımsız araç): oyun projesi gerekmez; yazı tipi + metin kaynağını seçmeniz yeterlidir;
  ttc/otc koleksiyonları otomatik olarak ayrılıp kalınlıklarına göre ayrı ayrı çıktılanır;
  asıl dosyanın üzerine asla yazılmaz, kullanılan karakter listesi de hediye

### Dil farkındalıklı yazı tipi küçültme

Yazı tipi küçültme, oyun metnini çeviri dillerine göre (tl/ klasörü) gruplandırır ve her zaman “çok dilli
paket” modunda çalışır: yazı tipleri artık tüm dillerin gliflerini toptan taşımaz. Küçültme üç kademede
yapılır: satır içi etiket yazı tipleri yalnızca gerçekten görüntüledikleri karakterleri korur; yalnızca bazı
dillerin başvurduğu yazı tipleri (ör. yalnızca Tayca için bir yazı tipi) gerçekten hizmet ettikleri dillere
daraltılır; kesin belirleme yapılamadığında otomatik olarak tam karakter setine geri dönülür — asla eksik
karakter kutusu oluşmaz. Eksik karakter uyarıları her yazı tipinin fiilen geçerli karakter setine göre
denetlenir; böylece dillere göre farklı yazı tipleri kullanan oyunlar artık yanlış alarmlara boğulmaz.

### Çalışma güvenceleri

- Çalışma sırasında istediğiniz an "Görevi durdur"a tıklayabilirsiniz (tamamlanan kısım korunur); görev başarısız olursa çökme dökümü otomatik kaydedilir
- Yeni sürüm çıktığında arayüz sizi uyarır (GitHub Releases ile karşılaştırılır)
- FFmpeg / 7-Zip eksikse arayüz somut kurulum adımlarını gösterir (winget komutu veya indirme bağlantısı)
- Çıkış yolu: sağ alttaki tepsi simgesine sağ tıklayın → Araçtan çık, ya da kenar çubuğunun sol altındaki "Araçtan çık" düğmesi
  (tarayıcı sekmesini kapatmak aracı durdurmaz)

## Başsız mod (betik/otomasyon için, baştan sona JSON çıktısı)

```
python cli.py env                                  # 环境体检
python cli.py analyze <路径> --mode project        # 分析
python cli.py optimize <路径> --preset balanced    # 优化
python cli.py full <工程路径> --platforms pc,mac   # 优化+打包一条龙
python cli.py slimfont <字体> <文本来源...>        # 独立字体瘦身
python cli.py slimapk <apk> --remap --gen-key      # APK 瘦身（图转WebP/音转OGG+重签名）
```

> Yapay zekâ asistanları / otomasyon betikleri: çağırmadan önce mutlaka [AGENTS.md](../AGENTS.md) dosyasını okuyun (güvenlik kuralları ve hata giderme dahildir).

## Sistem gereksinimleri

| Bağımlılık | Kullanım amacı | Açıklama |
|---|---|---|
| Ren'Py SDK | Paketleme, APK yeniden eşleme betiklerinin derlenmesi | Genellikle otomatik bulunur; bulunamazsa arayüzdeki "Ayarlar"dan belirtin |
| FFmpeg | Ses/video optimizasyonu | PATH'e kurmak ya da programın yanındaki bin klasörüne koymak yeterlidir |
| Java/JDK | Android paketleme, APK yeniden imzalama | Android paketleme için ilk kez Ren'Py başlatıcısında Android yapılandırmasını tamamlamak gerekir |

Arayüz hizmeti varsayılan olarak 127.0.0.1:52786'da dinler (az kullanılan bir port);
bu port doluyken sistem tarafından atanan boş bir porta otomatik geçer;
`RENPYTOOLS_PORT` ortam değişkeniyle başka bir port belirtilebilir.

## Güvenlik mekanizmalarına bakış

| Mekanizma | Açıklama |
|---|---|
| Çalışma kopyası | Varsayılan olarak önce kopyaya işlem yapılır, asıl dosyaların tek baytına bile dokunulmaz |
| Zorunlu yedekleme | "Asıl dosyaları doğrudan değiştir" seçiliyse önce tam yedek arşivi oluşturulur (kayıtlar dahil) |
| Küçülmediyse değiştirilmez | Her optimizasyon aracı önce geçici dosya yazar, boyutun gerçekten küçüldüğü doğrulanınca değiştirir |
| Referans denetimi | Senaryolarda doğrudan referansı bulunamayan dosyalar yalnızca yerinde sıkıştırılır, adları asla değiştirilmez |
| Motor klasörü koruması | Hazır ürün/APK modlarında renpy/, lib/, assets/x-renpy/ klasörlerine asla dokunulmaz |
| Silme, yalnızca işaretle | Referanssız olduğundan şüphelenilen dosyalar varsayılan olarak yalnızca rapora girer; seçenek açıldığında bile karantina bölgesine taşınır |
| Gereksiz dosya temizliği yalnızca yeniden üretilebilirleri siler | Önbellek/günlük/bayt kodu; asıl dosyayı doğrudan değiştirme modunda kayıtları korumak için otomatik atlanır |
| Görseller hurdaya çıkarılmaz | Ren'Py görselleri dosya adına göre otomatik yükler; referans bulunamaması kullanılmadığı anlamına gelmez |
| Kötü niyetli girdi koruması | Paket dizinleri beyaz listeyle çözümlenir; arşiv giriş yolları temizlenir (zip-slip savunması) |
| Yalnızca yerel kullanım | Hizmet yalnızca 127.0.0.1'i dinler, istek kaynağını doğrular; dış ağdan erişim mümkün değildir |
| Optimizasyon sonrası otomatik lint | Resmî statik denetim akışa gömülüdür, çıktı validation.txt olarak saklanır |
| Değişiklik listesi | Her çalıştırmada changelog.json yazılır, her değişiklik kaydedilir |

## Güvenlik sınırları

- Hizmet **yalnızca 127.0.0.1'i dinler** ("yalnızca bu bilgisayar" adresi): yerel ağdaki
  ve internetteki diğer cihazlar zaten bağlantı kuramaz; güvenlik duvarı yapılandırması
  gerekmez ve herhangi bir şekilde dışa açılması önerilmez;
- Araç "ağ erişimini aç" seçeneği sunmaz ve sunmayı planlamaz; kaynak kodu kendiniz
  değiştirirseniz dinleme adresini 0.0.0.0 ya da dış adreslere çevirmeniz
  **kesinlikle önerilmez** — arayüzde oturum açma doğrulaması yoktur; dışa açmak,
  bu bilgisayardaki dosya okuma/yazma yetkisini erişebilen herkese teslim etmek demektir;
- Araç kendiliğinden dış ağa erişmez; tek istisna "yeni sürüm kontrolü"dür
  (GitHub Releases ile karşılaştırılır, başarısız olursa sessizce atlanır, hiçbir özelliği etkilemez).

## Testler

```
.venv\Scripts\python -m pytest tests -q
```

Kapsam: RPA paketi okuma/yazma (eski ve yeni iki biçim ile kötü niyetli paket
engelleme dahil), referans yeniden yazma güvenliği, yazı tipi/görsel optimizasyonunun
asıl dosyaları bozmaması, rpyc çözümleme, APK inceltme (motor koruması / imza kaldırma /
x- önekli yol dönüşümü / anahtar üretimi), iptal ve çökme dökümleri, güvenli varsayılanlar,
denetim düzeltmelerinin gerileme testleri, arka uç yerel koruması ve arayüzün sekiz dil
sözlük bütünlüğü — toplam 114 test.

## Geliştirme

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
python main.py            # 启动图形界面
build_exe.bat             # 重新打包 exe
```

**Bakımcılar/Agent'lar önce şunları okuyun:**

- [docs/ARCHITECTURE.md](ARCHITECTURE.md): mimari taslak, güvenlik kırmızı çizgileri, genişletme rehberi
- [docs/BACKLOG.md](BACKLOG.md): talep arşivi ve yapılacaklar (yeni talepler önce buraya yazılır)
- [docs/STATUS.md](STATUS.md): devralma durumu ve saha testi geçmişi

## Çoklu dil desteği / Localization

| Dil | Arayüz | Belgeler | Durum |
|---|---|---|---|
| 简体中文 | ✅ varsayılan | ✅ bu belge | Yayında |
| English | ✅ | [README.en.md](README.en.md) | Yayında |
| Русский | ✅ | [README.ru.md](README.ru.md) | Yayında |
| Español | ✅ | [README.es.md](README.es.md) | Yayında |
| Português (BR) | ✅ | [README.pt.md](README.pt.md) | Yayında |
| Türkçe | ✅ | ✅ bu belge | Yayında |
| Deutsch | ✅ | [README.de.md](README.de.md) | Yayında |
| Français | ✅ | [README.fr.md](README.fr.md) | Yayında |

Yeni bir dil eklemek mi istiyorsunuz? [CONTRIBUTING.md](CONTRIBUTING.md) içindeki
"Çeviri rehberi"ne bakın — arayüze bir sözlük, belgelere bir README.<dil kodu>.md
eklemeniz yeterlidir.

## Lisans ve uyumluluk

- Bu proje **AGPL-3.0** lisansıyla yayınlanmıştır: yazılımı özgürce kullanabilir,
  değiştirebilir ve dağıtabilirsiniz; ancak değiştirilmiş sürümler (ağ üzerinden
  hizmet olarak sunulduğunda dahil) aynı lisansla açık kaynak olarak sunulmalıdır.
  Kendi oyununuzu kişisel olarak inceltmenin hiçbir kısıtlaması yoktur; açık kaynak
  yükümlülüğü yalnızca değiştirilmiş sürümü dağıttığınızda devreye girer.
- Üçüncü taraf bağımlılıkları ve biçim referans uygulamalarına ilişkin tam bildirimler:
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  (pystray LGPL uyumluluk açıklaması, Ren'Py biçimi teşekkürleri, harici program sınırları)
- Katkıda bulunmak için önce [CONTRIBUTING.md](CONTRIBUTING.md) dosyasını okuyun;
  güvenlik açıkları için [SECURITY.md](SECURITY.md) içindeki özel bildirim kanalını kullanın.
- Ren'Py, Tom Rothamel ve diğerlerinin tescilli markası/projesidir; bu proje onlarla
  bağlantılı değildir — yalnızca Ren'Py topluluğu için sunulmuş bağımsız bir üçüncü taraf aracıdır.
