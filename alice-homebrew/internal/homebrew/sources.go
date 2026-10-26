package homebrew

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

const OfficialCatalogURL = "https://raw.githubusercontent.com/hertebeznat-cell/alice-homebrew/main/docs/catalog/v2.json"
const OfficialCatalogKey = "bNgXwnJT9k5JhbY9QWfFRiTc/dgVlANhrJoTYAtNYAI="

type Source struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	URL      string    `json:"url"`
	Key      string    `json:"public_key"`
	Official bool      `json:"official"`
	Enabled  bool      `json:"enabled"`
	Removed  bool      `json:"removed,omitempty"`
	Revision uint64    `json:"revision"`
	Revoked  []string  `json:"revoked,omitempty"`
	Error    string    `json:"error,omitempty"`
	Updated  time.Time `json:"updated_at,omitempty"`
}

func officialSource(raw, key string) bool {
	return key == OfficialCatalogKey && officialURL(raw)
}
func officialURL(raw string) bool {
	for _, u := range []string{OfficialCatalogURL, strings.Replace(OfficialCatalogURL, "v2.json", "v1.json", 1), "https://hertebeznat-cell.github.io/alice-homebrew/catalog/v1.json", "https://hertebeznat-cell.github.io/alice-homebrew/catalog/v2.json"} {
		if raw == u {
			return true
		}
	}
	return false
}
func sourceID(raw, key string) string {
	if officialSource(raw, key) {
		return "official"
	}
	return Digest([]byte(raw + "\n" + key))[:24]
}
func NormalizeSource(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	u, e := url.Parse(raw)
	if e != nil || u.Host == "" || u.User != nil || u.Fragment != "" || u.RawQuery != "" {
		return "", errors.New("invalid source URL")
	}
	if strings.EqualFold(u.Host, "github.com") {
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
			return "", errors.New("use a GitHub repository URL or the direct catalog JSON URL")
		}
		u.Host = "raw.githubusercontent.com"
		u.Path = "/" + parts[0] + "/" + strings.TrimSuffix(parts[1], ".git") + "/main/docs/catalog/v2.json"
	} else if !strings.HasSuffix(u.Path, ".json") {
		u.Path = strings.TrimSuffix(u.Path, "/") + "/catalog/v2.json"
	}
	return u.String(), nil
}
func (m *Manager) sourceLocked(id string) *Source {
	for i := range m.state.Sources {
		if m.state.Sources[i].ID == id {
			return &m.state.Sources[i]
		}
	}
	return nil
}
func (m *Manager) initSources() error {
	if len(m.state.Sources) == 0 {
		if m.state.Settings.CatalogURL != "" {
			v := m.state.Settings
			m.state.Sources = []Source{{ID: sourceID(v.CatalogURL, v.CatalogKey), Name: "Imported catalog", URL: v.CatalogURL, Key: v.CatalogKey, Enabled: true, Revision: v.Revision, Revoked: append([]string{}, m.state.Revoked...)}}
			for _, p := range m.state.Plugins {
				if p.Reviewed {
					p.SourceID = m.state.Sources[0].ID
				}
				if p.Previous != nil && p.Previous.Reviewed {
					p.Previous.SourceID = m.state.Sources[0].ID
				}
			}
		} else {
			m.state.Sources = []Source{{ID: "official", Name: "Alice Homebrew", URL: OfficialCatalogURL, Key: OfficialCatalogKey, Official: true, Enabled: true}}
		}
	}
	if len(m.state.Sources) > 32 {
		return errors.New("too many source history records")
	}
	seen := map[string]bool{}
	for i := range m.state.Sources {
		s := &m.state.Sources[i]
		if _, e := m.storeURL(s.URL); e != nil {
			return e
		}
		key, e := base64.StdEncoding.DecodeString(s.Key)
		if e != nil || len(key) != ed25519.PublicKeySize || s.ID != sourceID(s.URL, s.Key) || seen[s.ID] {
			return errors.New("invalid saved source")
		}
		seen[s.ID] = true
		s.Official = officialSource(s.URL, s.Key)
		if s.Official {
			s.Name = "Alice Homebrew"
			s.URL = OfficialCatalogURL
		}
	}
	for _, p := range m.state.Plugins {
		for _, saved := range []*Saved{&p.Saved, p.Previous} {
			if saved == nil {
				continue
			}
			if source := m.sourceLocked(saved.SourceID); source != nil && saved.Reviewed {
				saved.SourceName = source.Name
				saved.SourceOfficial = source.Official
			} else {
				saved.SourceOfficial = false
			}
		}
	}
	m.mirrorSettingsLocked()
	return nil
}
func (m *Manager) mirrorSettingsLocked() {
	m.state.Settings = Settings{}
	for _, s := range m.state.Sources {
		if !s.Removed && s.Enabled {
			m.state.Settings = Settings{CatalogURL: s.URL, CatalogKey: s.Key, Revision: s.Revision}
			break
		}
	}
}
func (m *Manager) PreviewSource(ctx context.Context, raw, key, name string) (Source, *Catalog, error) {
	normalized, e := NormalizeSource(raw)
	if e != nil {
		return Source{}, nil, e
	}
	origin, e := m.storeURL(normalized)
	if e != nil {
		return Source{}, nil, e
	}
	data, e := m.download(ctx, normalized, origin, 2<<20)
	if e != nil && !strings.HasSuffix(strings.TrimSpace(raw), ".json") {
		normalized = strings.TrimSuffix(normalized, "v2.json") + "v1.json"
		origin, e = m.storeURL(normalized)
		if e == nil {
			data, e = m.download(ctx, normalized, origin, 2<<20)
		}
	}
	if e != nil {
		return Source{}, nil, e
	}
	if key == "" {
		var envelope Envelope
		if e = decode(data, &envelope); e != nil {
			return Source{}, nil, e
		}
		key = envelope.PublicKey
	}
	if officialURL(normalized) {
		if key != OfficialCatalogKey {
			return Source{}, nil, errors.New("official source public key does not match the built-in key")
		}
	}
	c, e := VerifyCatalog(data, key, 0)
	if e != nil {
		return Source{}, nil, e
	}
	name = strings.TrimSpace(name)
	if name == "" {
		name = origin.Hostname()
		if origin.Hostname() == "raw.githubusercontent.com" {
			parts := strings.Split(strings.Trim(origin.Path, "/"), "/")
			if len(parts) >= 2 {
				name = parts[0] + "/" + parts[1]
			}
		}
	}
	if len(name) > 80 || strings.ContainsAny(name, "\r\n\x00") {
		return Source{}, nil, errors.New("source name must be 1 to 80 characters")
	}
	s := Source{ID: sourceID(normalized, key), Name: name, URL: normalized, Key: key, Official: officialSource(normalized, key), Enabled: true, Revision: c.Revision, Revoked: append([]string{}, c.Revoked...), Updated: time.Now()}
	if s.Official {
		s.Name = "Alice Homebrew"
	}
	return s, &c, nil
}
func (m *Manager) AddSource(ctx context.Context, raw, key, name string, acknowledge bool) (Source, error) {
	s, c, e := m.PreviewSource(ctx, raw, key, name)
	if e != nil {
		return Source{}, e
	}
	if !s.Official && !acknowledge {
		return Source{}, errors.New("confirm adding this unofficial source")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	count := 0
	for _, item := range m.state.Sources {
		if !item.Removed {
			count++
		}
	}
	if count >= 8 {
		return Source{}, errors.New("maximum 8 sources")
	}
	old := clone(m.state.Sources)
	if existing := m.sourceLocked(s.ID); existing != nil {
		if !existing.Removed {
			return Source{}, errors.New("source already added")
		}
		if c.Revision < existing.Revision {
			return Source{}, errors.New("older source revision rejected")
		}
		*existing = s
	} else {
		if len(m.state.Sources) >= 32 {
			return Source{}, errors.New("source history limit reached")
		}
		m.state.Sources = append(m.state.Sources, s)
	}
	m.mirrorSettingsLocked()
	m.promoteOfficialLocked(s, c)
	m.applyRevocationsLocked(s.ID)
	if e = m.save(); e != nil {
		m.state.Sources = old
		m.mirrorSettingsLocked()
		return Source{}, e
	}
	m.catalogs[s.ID] = c
	m.rebuildCatalogLocked()
	return s, nil
}
func (m *Manager) ChangeSource(id string, enabled, remove bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.sourceLocked(id)
	if s == nil || s.Removed {
		return errors.New("source not found")
	}
	old := clone(*s)
	s.Enabled = enabled
	s.Removed = remove
	m.mirrorSettingsLocked()
	if e := m.save(); e != nil {
		*s = old
		m.mirrorSettingsLocked()
		return e
	}
	if remove {
		delete(m.catalogs, id)
	}
	m.rebuildCatalogLocked()
	return nil
}
func (m *Manager) RefreshSource(ctx context.Context, id string) error {
	m.mu.Lock()
	item := m.sourceLocked(id)
	if item == nil || item.Removed || !item.Enabled {
		m.mu.Unlock()
		return errors.New("source is disabled or removed")
	}
	s := clone(*item)
	m.mu.Unlock()
	origin, e := m.storeURL(s.URL)
	var raw []byte
	var c Catalog
	if e == nil {
		raw, e = m.download(ctx, s.URL, origin, 2<<20)
	}
	if e == nil {
		c, e = VerifyCatalog(raw, s.Key, s.Revision)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	current := m.sourceLocked(id)
	if current == nil || current.Removed || !current.Enabled {
		return errors.New("source changed while downloading")
	}
	if e != nil {
		current.Error = e.Error()
		m.CatalogError = e.Error()
		return e
	}
	if c.Revision < current.Revision {
		return errors.New("older source revision rejected")
	}
	oldRevision := current.Revision
	current.Revision = c.Revision
	current.Revoked = append([]string{}, c.Revoked...)
	current.Updated = time.Now()
	current.Error = ""
	m.promoteOfficialLocked(*current, &c)
	m.applyRevocationsLocked(id)
	m.mirrorSettingsLocked()
	if e = m.save(); e != nil {
		current.Revision = oldRevision
		current.Error = e.Error()
		delete(m.catalogs, id)
		m.rebuildCatalogLocked()
		return e
	}
	m.catalogs[id] = &c
	m.CatalogError = ""
	m.rebuildCatalogLocked()
	return nil
}
func (m *Manager) RefreshAll(ctx context.Context) error {
	m.mu.Lock()
	ids := []string{}
	for _, s := range m.state.Sources {
		if !s.Removed && s.Enabled {
			ids = append(ids, s.ID)
		}
	}
	m.mu.Unlock()
	if len(ids) == 0 {
		return errors.New("add a source first")
	}
	ctx, cancel := context.WithTimeout(ctx, 35*time.Second)
	defer cancel()
	var failures []error
	var wg sync.WaitGroup
	results := make(chan error, len(ids))
	slots := make(chan struct{}, 4)
	for _, id := range ids {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			select {
			case slots <- struct{}{}:
				defer func() { <-slots }()
			case <-ctx.Done():
				results <- ctx.Err()
				return
			}
			results <- m.RefreshSource(ctx, id)
		}(id)
	}
	wg.Wait()
	close(results)
	for e := range results {
		if e != nil {
			failures = append(failures, e)
		}
	}
	return errors.Join(failures...)
}
func (m *Manager) applyRevocationsLocked(sourceID string) {
	for id, p := range m.state.Plugins {
		if p.SourceID == sourceID && m.savedRevoked(p.Saved) {
			m.stop(id)
			p.Enabled = false
			p.LastError = "version revoked by the moderator"
		}
	}
}

// An identical local package can be recognized as officially reviewed without
// reinstalling it or changing the user's configuration or enabled state.
func (m *Manager) promoteOfficialLocked(source Source, c *Catalog) {
	if !officialSource(source.URL, source.Key) {
		return
	}
	for _, p := range m.state.Plugins {
		for _, saved := range []*Saved{&p.Saved, p.Previous} {
			if saved == nil {
				continue
			}
			for _, entry := range c.Entries {
				if saved.SHA256 == entry.SHA256 && saved.Manifest.ID == entry.Manifest.ID {
					saved.Reviewed = true
					saved.SourceID = source.ID
					saved.SourceName = source.Name
					saved.SourceOfficial = true
					break
				}
			}
		}
	}
}
func (m *Manager) savedRevoked(p Saved) bool {
	if p.SourceID != "" {
		if s := m.sourceLocked(p.SourceID); s != nil {
			for _, h := range s.Revoked {
				if h == p.SHA256 {
					return true
				}
			}
		}
		return false
	}
	return m.revoked(p.SHA256)
}
func (m *Manager) rebuildCatalogLocked() {
	combined := Catalog{Format: 1, Entries: []Entry{}, Revoked: []string{}}
	found := false
	for _, s := range m.state.Sources {
		c := m.catalogs[s.ID]
		if s.Removed || !s.Enabled || c == nil {
			continue
		}
		found = true
		if combined.Expires.IsZero() || c.Expires.Before(combined.Expires) {
			combined.Expires = c.Expires
		}
		if c.Revision > combined.Revision {
			combined.Revision = c.Revision
		}
		for _, entry := range c.Entries {
			copy := clone(entry)
			copy.SourceID = s.ID
			copy.SourceName = s.Name
			copy.SourceOfficial = s.Official
			combined.Entries = append(combined.Entries, copy)
		}
	}
	combined.Entries = latestCatalogEntries(combined.Entries)
	sort.SliceStable(combined.Entries, func(i, j int) bool {
		a, b := combined.Entries[i], combined.Entries[j]
		if a.SourceOfficial != b.SourceOfficial {
			return a.SourceOfficial
		}
		if a.Manifest.ID != b.Manifest.ID {
			return a.Manifest.ID < b.Manifest.ID
		}
		return a.Manifest.Version > b.Manifest.Version
	})
	if found {
		m.catalog = &combined
	} else {
		m.catalog = nil
	}
}
func (m *Manager) StageFrom(ctx context.Context, sourceID, hash string) (map[string]any, error) {
	m.mu.Lock()
	var selected *Entry
	var source Source
	if m.catalog != nil {
		for _, entry := range m.catalog.Entries {
			if entry.SHA256 == hash && (sourceID == "" || entry.SourceID == sourceID) {
				s := m.sourceLocked(entry.SourceID)
				c := m.catalogs[entry.SourceID]
				if s != nil && c != nil && time.Now().Before(c.Expires) {
					copy := clone(entry)
					selected = &copy
					source = clone(*s)
					break
				}
			}
		}
	}
	m.mu.Unlock()
	if selected == nil {
		return nil, errors.New("refresh the source; this version is not in its current catalog")
	}
	origin, e := m.storeURL(source.URL)
	if e != nil {
		return nil, e
	}
	rel, e := url.Parse(selected.Download)
	if e != nil {
		return nil, e
	}
	raw, e := m.download(ctx, origin.ResolveReference(rel).String(), origin, selected.Bytes)
	if e != nil {
		return nil, e
	}
	if len(raw) != selected.Bytes || Digest(raw) != hash {
		return nil, errors.New("downloaded package checksum or size mismatch")
	}
	p, e := Parse(raw)
	if e != nil {
		return nil, e
	}
	a, _ := json.Marshal(selected.Manifest)
	b, _ := json.Marshal(p.Manifest)
	if string(a) != string(b) {
		return nil, errors.New("package manifest differs from the reviewed manifest")
	}
	staged, e := m.Stage(raw, true)
	if e != nil {
		return nil, e
	}
	m.mu.Lock()
	t := m.tickets[staged["ticket"].(string)]
	t.SourceID = source.ID
	m.tickets[staged["ticket"].(string)] = t
	m.mu.Unlock()
	staged["source"] = source
	return staged, nil
}
