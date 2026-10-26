# Release 0.5.3

See [VALIDATION-0.5.3.md](VALIDATION-0.5.3.md) for targeted runtime cache checks. No device installation or full Homebrew suite was run. Earlier sections refer to historical releases.

---

# Release 0.5.2

See [VALIDATION-0.5.2.md](VALIDATION-0.5.2.md) for targeted TLS checks. No device installation or full Homebrew regression suite was run. The sections below refer to historical releases.

---

# Release 0.5.1

Compiled and packaged without tests or manual speaker installation, at the owner’s request. See [VALIDATION-0.5.1.md](VALIDATION-0.5.1.md). Prior sections describe historical releases only.

---

# Release 0.5.0

This release was compiled and packaged without running tests or manually installing it on the speaker, at the owner’s request. Music Ring and the changed AirPlay meter are experimental beta packages. See [VALIDATION-0.5.0.md](VALIDATION-0.5.0.md).

---

# Validation — 25 October 2026

- Go unit/integration tests cover schema rejection, archive paths, unsupported permissions/imports, WASM memory boundaries, timeout termination, function-table limits, local package consent, signed catalogs and pinned keys, immutable publication, updates, config migration, rollback, revocation persistence, authentication and Origin checks.
- The added fetch test proves that the service itself downloads a LAN package, checks its SHA-256 and still treats it as unreviewed. It checks that service credentials are not sent to the download host.
- The compiled Windows executable completed the full workflow using actual Ring Studio WASM: package → local moderation → signature → exported static Pages catalog in a subdirectory → service-side download → install → enable → animated frames → config changes.
- A separate personal package was served by HTTP and fetched/installed through `/api/fetch`, with explicit local-package consent. The station preview shows both official and personal packages with different review badges.
- Browser checks: login, generated fields, brightness slider, animation select, reverse switch, color input, config application and RU/EN. The static Pages preview verified its Ed25519 signature in the browser.
- Cross-built binaries: Windows AMD64, Linux AMD64, Linux ARM64. ARM64 is the architecture reported by the connected Mini 1.

Real Mini 1 hardware: installed the ARM64 service, verified unauthenticated station access (no admin-token.txt), loaded and ran the Ring Studio WASM module, checked successful I2C frame writes, and left the example disabled. Rebooted the speaker: the Homebrew init hook started the service, plugin state persisted, and HTTP access worked without login. The individual speaker's legacy voice/VPN software cleanup and Alice disablement are private maintenance steps, not part of the installer or public sources.

The separate moderator service on the maintainer PC retains its own access code. Station management does not require one. Catalog signing keys stay outside source and release archives.


## 0.3.0 native SDK / AirPlay beta

- Windows Go tests: API 2 ELF validation, architecture/entry-point checks,
  mandatory full-root declaration and explicit enable consent; inspection and
  installation do not run native code; legacy v1 catalog excludes native entries.
- ARM64 receiver smoke tests under local QEMU: Shairport 5.6.1 starts with a
  private ALSA null config, RTSP OPTIONS returns 200 and Apple-Challenge produces
  Apple-Response; RAOP TXT records include pw=false for an empty password.
- Go wrapper config escaping / descriptor validation tests pass.
- Real speaker hardware inventory was read without recording microphones,
  enabling sensors or changing hardware settings.
- Real iPhone audio streaming, microphone capture, GPIO and Bluetooth behavior
  have not been tested in this release. AirPlay is explicitly beta and is left
  uninstalled on the speaker for its owner's manual test.
- Complete dependency archives and receiver relinking materials accompany
  the native package in the separate source release asset.


## 0.4.0 manager updates

Tests cover persisted five-hour scheduling, disabled updates, version ordering/no downgrade, draft/prerelease rejection, pinned release repository and architecture, SHA-256 mismatch, staging cleanup and interrupted update recovery. A separate Linux real-process fixture upgrades 0.4.0 to a test-only 0.4.1 and exercises failed-start rollback while preserving an enabled plugin/config. The fixture binaries are test-only builds and are never published as release assets.
