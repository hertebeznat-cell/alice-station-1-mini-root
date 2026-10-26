package homebrew

import "golang.org/x/mod/semver"

func newerPluginVersion(candidate, current string) bool {
	return semver.IsValid("v"+candidate) && semver.IsValid("v"+current) && semver.Compare("v"+candidate, "v"+current) > 0
}

// Versions from different catalog authorities must remain separate choices.
func latestCatalogEntries(entries []Entry) []Entry {
	indices := map[string]int{}
	out := []Entry{}
	for _, entry := range entries {
		key := entry.SourceID + "\x00" + entry.Manifest.ID
		if i, exists := indices[key]; exists {
			if newerPluginVersion(entry.Manifest.Version, out[i].Manifest.Version) {
				out[i] = entry
			}
		} else {
			indices[key] = len(out)
			out = append(out, entry)
		}
	}
	return out
}

func (m *Manager) pluginUpdateLocked(p *Installed) *Entry {
	if m.catalog == nil || !p.Reviewed || p.SourceID == "" { return nil }
	var latest *Entry
	for _, entry := range m.catalog.Entries {
		if entry.SourceID != p.SourceID || entry.Manifest.ID != p.Manifest.ID || !newerPluginVersion(entry.Manifest.Version, p.Manifest.Version) { continue }
		if latest == nil || newerPluginVersion(entry.Manifest.Version, latest.Manifest.Version) {
			copy := clone(entry)
			latest = &copy
		}
	}
	return latest
}

// An already granted full-root permission still needs recorded native consent.
func expandedPluginAccess(old *Installed, next Manifest) bool {
	if next.Engine == "native" && (old.Manifest.Engine != "native" || !old.NativeConsent) { return true }
	for _, permission := range next.Permissions {
		if !Has(old.Manifest, permission) { return true }
	}
	return false
}

func (m *Manager) InstallUpdate(ticket, pluginID, expectedSHA string, permissions []string, acknowledge bool) error {
	return m.install(ticket, permissions, false, pluginID, expectedSHA, acknowledge)
}
