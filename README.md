# Station Mini Root

[English](README.en.md) · [Скачать](https://github.com/hertebeznat-cell/alice-station-1-mini-root/releases/latest) · [Как работает метод](docs/METHOD.md) · [Поддерживаемые прошивки](docs/FIRMWARE.md)

Программа получает **ADB root на Яндекс Станции Мини 1-го поколения** (YNDX-0004, Amlogic A113X) и ставит на неё [Alice Homebrew](https://github.com/hertebeznat-cell/alice-homebrew) — веб-панель с плагинами. Windows x64 и Linux x86_64, интерфейс на русском и английском.

Другие модели станций программа не поддерживает и сама это проверяет.

## Как пользоваться

1. Скачайте архив для своей системы и распакуйте.
   Windows: `StationMiniRoot.exe`. Linux: `chmod +x StationMiniRoot && ./StationMiniRoot`.
2. **«Установить все зависимости».** Программа найдёт или скачает ADB, в Windows подготовит WSL со сборщиком образа, в Linux — инструменты и правила udev. Окна UAC и доверия драйверу подтверждаются в системе.
3. Подключите Мини 1 USB-кабелем с передачей данных, когда программа попросит. Кнопки зажимать не нужно: программа ловит загрузчик и удерживает его.
4. **«Установить root».** Дальше всё автоматически: бэкап → сборка образа → запись → проверка → перезагрузка → проверка root → установка Alice Homebrew.
5. Откройте `http://IP-КОЛОНКИ:8088` — это панель Homebrew. Вход не нужен.

Во время записи не отключайте питание и кабель.

## Что меняется на колонке

```text
бэкап system (80 МиБ) → патч ADB и OTA → сборка образа из вашего бэкапа
→ запись system → полное обратное чтение и сверка SHA-256
→ загрузка → проверка uid=0 по ADB → установка Alice Homebrew в /data
```

- Перезаписывается **только раздел system**. Загрузчик, boot, nv и пользовательские данные не трогаются.
- Штатная Алиса и её службы продолжают работать.
- Отключаются автообновления прошивки Яндекса — иначе обновление вернёт заводской system и root пропадёт. Отключаются только известные механизмы обновления; если прошивка устроена иначе, сборка останавливается **до записи** ([подробнее](docs/FIRMWARE.md)).
- Если Homebrew не установился, повторное «Установить root» доустановит его без повторной прошивки.

## Бэкап и восстановление

Рабочая папка:

- Windows: `%LOCALAPPDATA%\StationMiniRoot\work`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/StationMiniRoot/work`

В ней лежат личный ADB-ключ, исходный бэкап system и `session.json`. **Сохраните её и не публикуйте.**

«Восстановить бэкап» записывает исходный system обратно (с проверкой обратным чтением) и возвращает заводское поведение обновлений. Homebrew живёт в `/data`, восстановление system его не удаляет.

## Обновить Homebrew без прошивки

Подключите уже рутированную колонку по ADB к тому же компьютеру и нажмите «Обновить только Homebrew (ADB root)». Нужна прежняя рабочая папка с ADB-ключом. Сам Homebrew дальше обновляется через свою панель.

## Сборка из исходников

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt pyinstaller==6.16.0
.venv/bin/python scripts/prepare-homebrew.py   # скачивает закреплённый релиз Homebrew и сверяет SHA-256
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python scripts/build-app.py
```

В Windows вместо `.venv/bin/python` — `.venv\Scripts\python`. Для Windows-сборки сначала соберите WinUSB-помощник: [native/README.md](native/README.md). Запуск из исходников: `python station_mini_root.py` (Linux: `bash scripts/setup-linux.sh`, затем `bash start-linux.sh`).

Сборка образа вручную из **своего** бэкапа:

```bash
adb keygen /path/to/adbkey
bash scripts/build_image.sh system-backup.bin /path/to/adbkey.pub output.ubi
```

### Устройство кода

| Модуль | Что делает |
| --- | --- |
| `station_root/mini1.py` | Всё, что известно о Мини 1: USB ID, размер раздела, геометрия UBI, пути прошивки |
| `station_root/data/known-firmware.json` | Белый список проверенных бинарников обновления |
| `station_root/usb_transport.py` | Чтение и запись раздела system через загрузчик |
| `station_root/image_builder.py` | Патч rootfs и сборка UBI (в Linux/WSL под fakeroot) |
| `station_root/adb.py` | Проверка root по ADB, не трогая ваш ADB-сервер |
| `station_root/workflow.py` | Порядок шагов: бэкап → образ → запись → проверка → Homebrew |
| `station_root/gui.py` | Окно приложения |

## Лицензии

Оригинальный код опубликован для ознакомления на [закрытых условиях](LICENSE): готовую программу можно использовать лично и некоммерчески, распространение и изменённые версии — только с письменного разрешения автора. Это не open-source.

USB-транспорт (MIT, на основе pyamlboot), WinUSB-помощник и libusb (LGPL), Alice Homebrew (MIT) и другие компоненты сохраняют свои лицензии: [THIRD_PARTY.md](THIRD_PARTY.md), `licenses/`, `native/upstream/`.

Прошивки Яндекса, готовые образы, ключи и дампы в репозиторий и релизы не входят.
