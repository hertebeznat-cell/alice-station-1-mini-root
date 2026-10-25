# Происхождение и зависимости

Создано по локальным исследовательским скриптам владельца. Ограниченный Optimus-транспорт следует локальному pyamlboot; полный pyamlboot и его загрузчики не включены.

Благодарности: Neil Armstrong / BayLibre (copyright 2018), SaluteDevices, JetHome за реализацию и описание Optimus. [pyamlboot](https://github.com/superna9999/pyamlboot), SPDX Apache-2.0 OR MIT / GPL-2.0 OR MIT. MIT-уведомление: `licenses/pyamlboot-MIT.txt`.

Зависимости сохраняют собственные лицензии:

- [PyUSB](https://github.com/pyusb/pyusb): BSD-3-Clause.
- [libusb-package](https://github.com/pyusb/libusb-package) и [libusb](https://libusb.info/): лицензия пакета и LGPL-2.1-or-later для libusb.
- [ubi-reader](https://github.com/onekey-sec/ubi_reader): GPL-3.0-or-later; отдельный Linux-процесс, исходники не включены.
- [mtd-utils](https://git.infraroot.at/mtd-utils.git): отдельные программы mkfs.ubifs / ubinize.
- [fakeroot](https://tracker.debian.org/pkg/fakeroot): отдельный инструмент сохранения метаданных.
- [PyInstaller](https://pyinstaller.org/): GPL с исключением для bootloader.
- Python и Tcl/Tk: лицензии их дистрибутивов. Android Platform-Tools скачиваются приложением с Google при отсутствии; в распространяемые архивы не включены.

Личные ключи, дампы NAND, аккаунты, настройки, Yandex system/OTA-образы в исходный пакет не входят.

- [libwdi 1.5.1](https://github.com/pbatard/libwdi/tree/v1.5.1): LGPL-3.0-or-later. Нативный помощник статически связан с библиотекой; полные исходники, рецепт повторной сборки и лицензии включены в `native/` и `licenses/libwdi/`. Помощник можно пересобрать с изменённой библиотекой и обновить его SHA-256-манифест.
- Microsoft WDK 8 WinUSB/WDF redistributables: оригинальные компоненты Microsoft, встроенные в помощник libwdi. Условия распространения: `licenses/libwdi/Microsoft-WDK-redist-license.rtf`.


## Bundled Alice Homebrew

The ARM64 Homebrew service includes wazero v1.12.0 (Apache-2.0), golang.org/x/sys v0.44.0 (BSD-3-Clause), and the Go runtime (BSD). Their full licenses and wazero NOTICE are distributed under alice-homebrew/licenses/. See alice-homebrew/THIRD_PARTY.md. Homebrew source is included in the source archive.


## Original application and source exceptions

Original application code is source-available under the proprietary terms in
LICENSE. This does not restrict the independent MIT transport file
station_root/usb_transport.py, the LGPL native helper/libwdi, libusb, or MIT
Alice Homebrew. Their original notices remain in this source distribution.

Complete libusb 1.0.26 and libwdi 1.5.1 source archives are included under
native/upstream/; provenance and SHA-256 are recorded in SOURCES.json. The
Apache-2.0 libusb-package wrapper is available from https://github.com/pyusb/libusb-package.
You may rebuild those libraries, install a modified libusb-package in the build
venv and rebuild the application. The native driver helper can likewise be
rebuilt with a modified libwdi and a regenerated SHA-256 manifest. Component
licenses override the original application's restrictions where applicable.
