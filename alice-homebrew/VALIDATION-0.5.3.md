# Validation: Homebrew 0.5.3

Scope: automatic cleanup of obsolete plugin runtime executables. No firmware or rooting changes.

Targeted checks cover removal of old version directories, retention of the current executable and rollback package, preservation of configuration and plugin data, rollback and restart from the retained package, cleanup of existing caches on manager startup, repeated cleanup, invalid plugin paths, and symbolic links. The same checks are run on Windows and Linux; symlink cases may be skipped on Windows where link creation is unavailable.

Release builds target Windows AMD64, Linux AMD64 and Linux ARM64. Published assets are checked against their local SHA-256 digests. Existing plugin packages and the signed catalogue are reused unchanged.

The full Homebrew suite and installation on a physical speaker are not part of this release validation. Cleanup errors are best-effort: an inaccessible cache is retained rather than preventing manager startup or plugin updates. User data outside the reserved runtimes directory is not cleaned.
