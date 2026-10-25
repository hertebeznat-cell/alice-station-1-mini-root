package homebrew

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

type Entry struct {
	Manifest       Manifest  `json:"manifest"`
	SHA256         string    `json:"sha256"`
	Bytes          int       `json:"bytes"`
	Download       string    `json:"download"`
	ReviewedAt     time.Time `json:"reviewed_at"`
	SourceID       string    `json:"source_id,omitempty"`
	SourceName     string    `json:"source_name,omitempty"`
	SourceOfficial bool      `json:"source_official,omitempty"`
}
type Catalog struct {
	Format    int       `json:"format"`
	Revision  uint64    `json:"revision"`
	Generated time.Time `json:"generated_at"`
	Expires   time.Time `json:"expires_at"`
	Entries   []Entry   `json:"entries"`
	Revoked   []string  `json:"revoked"`
}
type Envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
	PublicKey string `json:"public_key"`
}

func SignCatalog(c Catalog, key ed25519.PrivateKey) (Envelope, error) {
	b, e := json.Marshal(c)
	if e != nil {
		return Envelope{}, e
	}
	return Envelope{base64.StdEncoding.EncodeToString(b), base64.StdEncoding.EncodeToString(ed25519.Sign(key, b)), base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey))}, nil
}
func VerifyCatalog(raw []byte, pinned string, minRevision uint64) (Catalog, error) {
	var c Catalog
	var signed Envelope
	if e := decode(raw, &signed); e != nil {
		return c, e
	}
	key, e := base64.StdEncoding.DecodeString(pinned)
	if e != nil || len(key) != ed25519.PublicKeySize {
		return c, errors.New("invalid pinned catalog key")
	}
	b, e := base64.StdEncoding.DecodeString(signed.Payload)
	if e != nil {
		return c, e
	}
	sig, e := base64.StdEncoding.DecodeString(signed.Signature)
	if e != nil || !ed25519.Verify(key, b, sig) {
		return c, errors.New("catalog signature does not match the pinned moderator key")
	}
	if e = decode(b, &c); e != nil {
		return c, e
	}
	now := time.Now()
	if c.Format != 1 || c.Revision < minRevision || c.Generated.After(now.Add(5*time.Minute)) || !c.Expires.After(now) || c.Expires.After(c.Generated.Add(366*24*time.Hour)) {
		return c, errors.New("expired, unsupported or older catalog")
	}
	if len(c.Entries) > 1000 || len(c.Revoked) > 4000 {
		return c, errors.New("catalog too large")
	}
	seen := map[string]bool{}
	for _, entry := range c.Entries {
		if ValidateManifest(entry.Manifest) != nil || !validHash(entry.SHA256) || entry.Bytes < 1 || entry.Bytes > MaxPackage || seen[entry.Manifest.ID+"@"+entry.Manifest.Version] {
			return c, errors.New("invalid or duplicate catalog entry")
		}
		seen[entry.Manifest.ID+"@"+entry.Manifest.Version] = true
	}
	for _, hash := range c.Revoked {
		if !validHash(hash) {
			return c, errors.New("invalid revocation hash")
		}
	}
	return c, nil
}
func (m *Manager) storeURL(raw string) (*url.URL, error) {
	u, e := url.Parse(raw)
	if e != nil || u.Host == "" || u.User != nil || u.Fragment != "" {
		return nil, errors.New("invalid catalog URL")
	}
	if _, e = manualURL(raw, m.AllowLocalStore); e != nil {
		return nil, e
	}
	return u, nil
}
func (m *Manager) download(ctx context.Context, raw string, origin *url.URL, limit int) ([]byte, error) {
	u, e := m.storeURL(raw)
	if e != nil {
		return nil, e
	}
	if u.Scheme != origin.Scheme || u.Host != origin.Host {
		return nil, errors.New("downloads must stay on the catalog origin")
	}
	client := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) > 3 || req.URL.Scheme != origin.Scheme || req.URL.Host != origin.Host {
			return errors.New("catalog redirect left the trusted origin")
		}
		return nil
	}}
	req, e := http.NewRequestWithContext(ctx, "GET", u.String(), nil)
	if e != nil {
		return nil, e
	}
	response, e := client.Do(req)
	if e != nil {
		return nil, e
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("store HTTP %d", response.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(response.Body, int64(limit+1)))
	if e != nil {
		return nil, e
	}
	if len(b) > limit {
		return nil, errors.New("store response exceeds the size limit")
	}
	return b, nil
}

// SetStore keeps compatibility with the original single-source API. User-facing
// additions go through PreviewSource/AddSource and require unofficial consent.
func (m *Manager) SetStore(raw, key string) error {
	if raw != "" {
		if _, e := m.storeURL(raw); e != nil {
			return e
		}
		k, e := base64.StdEncoding.DecodeString(key)
		if e != nil || len(k) != ed25519.PublicKeySize {
			return errors.New("public key must be 32 bytes encoded as Base64")
		}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	old := clone(m.state)
	m.state.Sources = []Source{}
	if raw != "" {
		m.state.Sources = append(m.state.Sources, Source{ID: sourceID(raw, key), Name: "Catalog", URL: raw, Key: key, Official: officialSource(raw, key), Enabled: true})
	}
	m.mirrorSettingsLocked()
	if e := m.save(); e != nil {
		m.state = old
		return e
	}
	m.catalogs = map[string]*Catalog{}
	m.catalog = nil
	m.CatalogError = ""
	return nil
}
func (m *Manager) Refresh(ctx context.Context) error { return m.RefreshAll(ctx) }
func (m *Manager) StageEntry(ctx context.Context, hash string) (map[string]any, error) {
	return m.StageFrom(ctx, "", hash)
}
