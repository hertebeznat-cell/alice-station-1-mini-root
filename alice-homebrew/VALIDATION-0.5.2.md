# Validation: 0.5.2 TLS repair

Targeted local TLS regression checks cover an absent system store, additional roots without mutating the original pool, unknown CA rejection, hostname checks and expiry checks. A separate Go HTTPS probe used only the embedded Mozilla PEM (no OS roots) and verified HTTP 200 and certificate chains for the official raw GitHub catalog and GitHub Releases API. No physical speaker installation or firmware flashing was performed. The full Homebrew test suite was not run for this patch.
