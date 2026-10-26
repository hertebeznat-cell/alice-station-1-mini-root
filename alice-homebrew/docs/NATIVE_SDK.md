# Нативные плагины: оборудование Мини 1

Homebrew 0.3 добавляет SDK 2 для нативных Linux ARM64 сервисов. Он нужен для AirPlay, микрофона, динамика, датчика расстояния, кнопок, подсветки, сети и других интерфейсов Linux. WASM SDK 1 остаётся совместимым и изолированным.

## Два формата

| Формат | Возможности | Выполнение |
|---|---|---|
| API 1, `plugin.wasm` | `led.ring`, `log.write`, конфиг | WASM: 2 МиБ памяти, ограничение времени кадра |
| API 2, `engine: "native"`, `plugin.elf` | ALSA, input, I²C, GPIO, UART, сеть, файлы, остальные доступные интерфейсы Linux | Полный root, без песочницы |

Нативному плагину обязательно разрешение `system.full-access`. Менеджер показывает предупреждение при установке и требует отдельного подтверждения перед включением. Список `capabilities` описывает назначение плагина, но **не ограничивает** системные права. Проверенная подпись подтверждает автора каталога и целостность пакета, а не безопасность произвольного root-кода.

При загрузке пакета и модерации ELF только разбирается и проверяется, код не запускается. Установка сохраняет пакет выключенным. Запуск происходит после нажатия «Включить»; после этого включённый сервис автоматически запускается при старте Homebrew. Удаление его данных выполняется только через явное действие удаления плагина.

## Упаковка

Архив `.aliceplugin`: `manifest.json`, статический `plugin.elf`, необязательные `README.md`, `LICENSE`. Подкаталоги, симлинки и дополнительные файлы запрещены. Ресурсы встройте в ELF. Нужны ELF64 little endian AArch64 и статическая линковка без внешних библиотек/интерпретатора. Формат с динамическими библиотеками системной прошивки намеренно не используется: версии libc на колонках различаются.

Максимум: архив 16 МиБ, ELF 24 МиБ. `runtime_storage_bytes` задаёт дополнительное место для файлов, которые плагин извлечёт сам; менеджер проверяет сумму архива, ELF и этой величины плюс резерв 8 МиБ. Это оценка установки, не квота файловой системы. Автор должен ограничивать размер своего кеша.

```json
{
  "format": 1,
  "id": "my-native-plugin",
  "version": "1.0.0",
  "api": 2,
  "engine": "native",
  "title": {"ru": "Мой плагин", "en": "My plugin"},
  "description": {"ru": "Работа с оборудованием", "en": "Hardware service"},
  "author": "Your name",
  "permissions": ["system.full-access", "log.write"],
  "capabilities": ["audio.capture", "sensor.proximity", "input.buttons"],
  "runtime_storage_bytes": 0,
  "config": [],
  "module_sha256": "Заполняется командой pack"
}
```

Сборка Go: `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -o plugin.elf .`. Затем `alice-homebrew pack ПАПКА MyPlugin.aliceplugin`. Для C/C++ используйте статическую ARM64 сборку и распространяйте соответствующие лицензии/исходники зависимостей.

## Жизненный цикл и конфигурация

Менеджер передаёт:

- `ALICE_CONFIG_PATH`: приватный JSON с текущими значениями конфига.
- `ALICE_PLUGIN_DATA`: каталог постоянных данных и рабочий каталог процесса.
- `ALICE_HARDWARE_PATH`: JSON со списком реально обнаруженного оборудования.
- `ALICE_PLUGIN_ID`: идентификатор плагина.

stdout/stderr попадают в ограниченный журнал в интерфейсе. Конфиг сохраняется визуальными полями схемы, как в WASM. После сохранения настроек включённый нативный сервис перезапускается. Обрабатывайте SIGTERM и освобождайте ALSA/input/I²C; через 1,5 секунды менеджер завершает оставшуюся группу процессов. Не создавайте отдельные демоны/сессии и не меняйте системный автозапуск. Менеджер запускает конкретный ELF без shell. Сбой сервиса выключает плагин с сообщением в интерфейсе.

## Обнаруженное оборудование YNDX-0004

Это результаты чтения интерфейсов одной Мини первого поколения. Имена устройств важнее номеров: автор должен проверять наличие и параметры оборудования при запуске, а не обещать работу на всех ревизиях.

| Оборудование | Интерфейс |
|---|---|
| Динамик / звуковой тракт | ALSA card 0 AML-AUGESOUND, PCM 0 TDM-B и PCM 1 TDM-C playback |
| Микрофоны | ALSA PCM 2 PDM capture; ранее использовались 8 каналов, 48000 Гц, S32_LE |
| Датчик расстояния | Linux input с именем `STM VL53L0 proximity sensor`, ABS-события; sysfs `enable_ps_sensor`, `set_delay_ms`, `set_range_profile`, `set_timing_budget` |
| Кнопки | Linux input `adc_keypad`, EV_KEY |
| Наушники | Linux input `AML-AUGESOUND Headphones`, EV_SW |
| Подсветка | `/dev/i2c-0`, адрес 0x3c, 12 RGB светодиодов; backend в `internal/homebrew/ring_linux.go` |
| I²C | `/dev/i2c-0`, `/dev/i2c-1` |
| GPIO | `/dev/gpiochip0`, `/dev/gpiochip1`, стандартные Linux GPIO ioctl |
| UART | `/dev/ttyS0`, `/dev/ttyS1`, `/dev/ttyS3`; сначала проверьте назначение линии |
| Температура, LED, Bluetooth | обнаружение через `/sys/class`, доступность зависит от прошивки и драйверов |
| Сеть | обычные Linux TCP/UDP, multicast, интерфейсы; не меняйте Wi-Fi/маршрутизацию без назначения плагина |

Настройки → «Оборудование колонки» или `GET /api/hardware` показывают текущий список. Сам просмотр не включает датчик и не записывает микрофон.

`sdk/hardware` содержит `Inputs`, `FindInput`, `ReadInput` для ARM64 `input_event` и `OpenI2C`/`Read`/`Write`/`WriteRegister`. Пример чтения датчика:

```go
path, err := hardware.FindInput("STM VL53L0 proximity sensor")
if err != nil { log.Fatal(err) }
f, err := os.Open(path)
if err != nil { log.Fatal(err) }
defer f.Close()
for {
    event, err := hardware.ReadInput(f)
    if err != nil { log.Fatal(err) }
    if event.Type == 3 { log.Printf("ABS code=%d value=%d", event.Code, event.Value) }
}
```

Не фиксируйте `/dev/input/event0`: номер меняется. Датчик может быть выключен драйвером; включение через sysfs — отдельное явное действие вашего плагина, с восстановлением исходного состояния после завершения. Единицы и коды ABS определяйте по драйверу/показаниям, не считайте любое значение миллиметрами без проверки.

## Микрофон и воспроизведение

`sdk/audio/pcm.c` — небольшой ALSA helper для явного захвата/воспроизведения сырого PCM, который автор может собрать и встроить в свой плагин. Примеры после сборки:

```sh
alice-pcm capture hw:0,2 48000 8 S32_LE 1024 > recording.pcm
alice-pcm playback plughw:0,0 48000 2 S32_LE 1024 < stereo.pcm
```

Первый пример **начинает запись микрофонов**. В пользовательском плагине делайте видимый переключатель записи и ограничивайте длительность/размер. PDM выдаёт многоканальный сигнал: сведение каналов, шумоподавление и эхокомпенсация — отдельная обработка, её SDK не обещает автоматически. Аппаратный mute, поддерживаемые форматы и маршрутизацию проверяйте на устройстве.

Если ALSA ссылается на отсутствующий PCM 3 из штатного `asound.conf`, используйте конкретный `hw`/`plughw` или собственный `ALSA_CONFIG_PATH`. AirPlay поставляет собственную минимальную конфигурацию и не переписывает системную.

## Совместное использование

Не захватывайте input через EVIOCGRAB без необходимости. ALSA hardware может быть занят Алисой или другим плагином; верните понятную ошибку, не убивайте чужие процессы. Запрашивайте `led.ring`, если используете подсветку: менеджер не запустит одновременно два плагина с этим разрешением. Для root-плагинов это договорённость, не технический запрет обхода.

Полный root позволяет работать и с другими устройствами, но наличие доступа не равно готовому драйверу. Публикуйте проверенные устройства, ограничения и версии прошивки в README своего плагина. Реальная запись микрофона, GPIO, Bluetooth и поток AirPlay в этой версии не проверялись на устройстве автоматически.

## Каталоги

`catalog/v1.json` содержит только API 1 для старых менеджеров; `catalog/v2.json` содержит API 1 и API 2. Оба подписываются тем же ключом. Homebrew 0.3 переводит встроенный официальный источник на v2, сохраняя настройки и историю отзыва. Собственный источник экспортирует обе версии. Формат конверта подписи остаётся 1.

## Native LED frames and audio levels (Homebrew 0.5.0)

A native plugin declaring `led.ring` may write one newline-terminated stdout record `@alice-ring BASE64`, containing exactly 36 bytes of BGR LED data. The native runner keeps only the latest frame; the manager owns I²C, applies it at up to 20 Hz and exposes the live preview. LED ownership conflicts and shutdown clearing use the same rules as WASM plugins. Regular stdout/stderr still goes to bounded logs.

Homebrew listens only on `127.0.0.1:18789/udp`. A playback source may send 64 bytes: ASCII `ALICEAU1`, then 14 LE float32 values in [0,1]: RMS, peak and 12 frequency bands. Only summaries are accepted, no raw audio. A local plugin sends ASCII `ALICE_LEVELS1` to receive a JSON snapshot; web UI can use `GET /api/audio`. Data expires after 400 ms. This is intended for one speaker manager instance. Root plugins are trusted; this local protocol is not an isolation boundary.

AirPlay beta.2 emits these summaries from its S32_LE ALSA output. Music Ring beta.1 consumes them without opening the microphone. Other players need their own meter integration. The new implementation was released without tests at the owner’s request; no measured synchronization accuracy or universal player compatibility is claimed.
