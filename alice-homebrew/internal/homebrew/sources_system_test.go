package homebrew

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type catalogFixture struct {
	Catalog Catalog
	Key     ed25519.PrivateKey
	Package *Package
	Server  *httptest.Server
}

func sourceFixture(t *testing.T, p *Package) *catalogFixture {
	t.Helper()
	_, key, _ := ed25519.GenerateKey(rand.Reader)
	f := &catalogFixture{Key: key, Package: p, Catalog: Catalog{Format: 1, Revision: 1, Generated: time.Now(), Expires: time.Now().Add(time.Hour), Entries: []Entry{{Manifest: p.Manifest, SHA256: p.SHA256, Bytes: len(p.Raw), Download: "../packages/" + p.SHA256 + ".aliceplugin", ReviewedAt: time.Now()}}, Revoked: []string{}}}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || len(r.Cookies()) > 0 {
			t.Error("credentials sent to a source")
		}
		if r.URL.Path == "/catalog/v1.json" {
			env, e := SignCatalog(f.Catalog, f.Key)
			if e != nil {
				t.Error(e)
			}
			json.NewEncoder(w).Encode(env)
			return
		}
		if r.URL.Path == "/packages/"+p.SHA256+".aliceplugin" {
			w.Write(p.Raw)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(f.Server.Close)
	return f
}
func TestOfficialSourceRequiresKnownAddressAndPinnedKey(t *testing.T) {
	if !officialSource(OfficialCatalogURL, OfficialCatalogKey) {
		t.Fatal("built-in source not official")
	}
	for _, c := range [][2]string{{"https://example.org/catalog/v1.json", OfficialCatalogKey}, {OfficialCatalogURL, base64.StdEncoding.EncodeToString(make([]byte, 32))}, {"https://github.com/unrelated/official", OfficialCatalogKey}} {
		if officialSource(c[0], c[1]) {
			t.Fatal("spoofed official source", c[0])
		}
	}
	normalized, e := NormalizeSource("https://github.com/hertebeznat-cell/alice-homebrew")
	if e != nil || normalized != OfficialCatalogURL {
		t.Fatal(normalized, e)
	}
	m := manager(t)
	if len(m.state.Sources) != 1 || !m.state.Sources[0].Official {
		t.Fatal("official source was not preconfigured")
	}
}
func TestUnofficialSourceConsentAndProvenance(t *testing.T) {
	p := mustPackage(t, "1.0.0")
	f := sourceFixture(t, p)
	m := manager(t)
	// A source cannot grant itself an official badge through signed entry metadata.
	f.Catalog.Entries[0].SourceOfficial = true
	f.Catalog.Entries[0].SourceID = "official"
	preview, _, e := m.PreviewSource(context.Background(), f.Server.URL, "", "My source")
	if e != nil || preview.Official {
		t.Fatal(preview, e)
	}
	if _, e = m.AddSource(context.Background(), f.Server.URL, "", "My source", false); e == nil {
		t.Fatal("unofficial source added without consent")
	}
	if len(m.state.Sources) != 1 {
		t.Fatal("failed consent changed source settings")
	}
	source, e := m.AddSource(context.Background(), f.Server.URL, preview.Key, "My source", true)
	if e != nil {
		t.Fatal(e)
	}
	if source.Official {
		t.Fatal("community source became official")
	}
	staged, e := m.StageFrom(context.Background(), source.ID, p.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e != nil {
		t.Fatal(e)
	}
	installed := m.state.Plugins[p.Manifest.ID]
	if !installed.Reviewed || installed.SourceOfficial || installed.SourceID != source.ID {
		t.Fatal("incorrect source provenance", installed.Saved)
	}
	for _, entry := range m.catalog.Entries {
		if entry.SourceID == source.ID && entry.SourceOfficial {
			t.Fatal("source metadata spoofed an official badge")
		}
	}
}
func TestSourcesHaveIndependentRevisionsAndRevocations(t *testing.T) {
	p := mustPackage(t, "1.0.0")
	a, b := sourceFixture(t, p), sourceFixture(t, mustPackage(t, "2.0.0"))
	m := manager(t)
	ctx := context.Background()
	sa, e := m.AddSource(ctx, a.Server.URL, "", "A", true)
	if e != nil {
		t.Fatal(e)
	}
	sb, e := m.AddSource(ctx, b.Server.URL, "", "B", true)
	if e != nil {
		t.Fatal(e)
	}
	staged, e := m.StageFrom(ctx, sa.ID, p.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(p.Manifest.ID, true); e != nil {
		t.Fatal(e)
	}
	b.Catalog.Revision = 2
	b.Catalog.Revoked = []string{p.SHA256}
	if e = m.RefreshSource(ctx, sb.ID); e != nil {
		t.Fatal(e)
	}
	if !m.state.Plugins[p.Manifest.ID].Enabled {
		t.Fatal("another source revoked this source's plugin")
	}
	a.Catalog.Revision = 2
	a.Catalog.Revoked = []string{p.SHA256}
	if e = m.RefreshSource(ctx, sa.ID); e != nil {
		t.Fatal(e)
	}
	if m.state.Plugins[p.Manifest.ID].Enabled {
		t.Fatal("owner revocation did not stop the plugin")
	}
	if e = m.ChangeSource(sa.ID, false, true); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(p.Manifest.ID, true); e == nil {
		t.Fatal("removing source bypassed a known revocation")
	}
	a.Catalog.Revision = 1
	if _, e = m.AddSource(ctx, a.Server.URL, "", "A", true); e == nil {
		t.Fatal("source deletion bypassed revision rollback protection")
	}
}
func TestStorageFailureOffersLargestPluginsAndDoesNotWrite(t *testing.T) {
	m := manager(t)
	pkg := mustPackage(t, "1.0.0")
	for i, size := range []int{200, 900} {
		hash := Digest([]byte{byte(i)})
		manifest := pkg.Manifest
		manifest.ID = []string{"local.small", "local.large"}[i]
		m.state.Plugins[manifest.ID] = &Installed{Saved: Saved{Manifest: manifest, SHA256: hash, Config: map[string]any{"brightness": float64(25)}}}
		if e := os.WriteFile(filepath.Join(m.dir, "packages", hash+".aliceplugin"), make([]byte, size), 0600); e != nil {
			t.Fatal(e)
		}
	}
	free := uint64(100)
	m.diskProbe = func(string) DiskUsage { return DiskUsage{Available: true, AvailableBytes: free, TotalBytes: 100 << 20} }
	staged, e := m.Stage(pkg.Raw, false)
	if e != nil {
		t.Fatal(e)
	}
	plan := staged["storage"].(StoragePlan)
	if plan.Fits || plan.Candidates[0].ID != "local.large" || plan.Candidates[0].Bytes != 900 {
		t.Fatal(plan)
	}
	e = m.Install(staged["ticket"].(string), nil, true)
	var low *StorageError
	if !errors.As(e, &low) {
		t.Fatal("expected structured storage failure", e)
	}
	if _, e = os.Stat(filepath.Join(m.dir, "packages", pkg.SHA256+".aliceplugin")); !os.IsNotExist(e) {
		t.Fatal("package written despite insufficient storage")
	}
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/install", strings.NewReader(`{"ticket":"`+staged["ticket"].(string)+`","permissions":[],"acknowledge_local":true}`))
	request.Header.Set("Content-Type", "application/json")
	NewServer(m, nil, "").ServeHTTP(recorder, request)
	if recorder.Code != 507 || !strings.Contains(recorder.Body.String(), `"code":"insufficient_storage"`) {
		t.Fatal(recorder.Code, recorder.Body.String())
	}
	free = StorageReserve + uint64(len(pkg.Raw)) + 1024
	if e = m.Install(staged["ticket"].(string), nil, true); e != nil {
		t.Fatal("confirmation could not be retried after freeing space", e)
	}
}
func TestLegacyCatalogMigrationAndPersistentConfig(t *testing.T) {
	m := manager(t)
	p := mustPackage(t, "1.0.0")
	fixture := sourceFixture(t, p)
	if e := m.SetStore(fixture.Server.URL+"/catalog/v1.json", base64.StdEncoding.EncodeToString(fixture.Key.Public().(ed25519.PublicKey))); e != nil {
		t.Fatal(e)
	}
	if e := m.Refresh(context.Background()); e != nil {
		t.Fatal(e)
	}
	staged, e := m.StageEntry(context.Background(), p.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e != nil {
		t.Fatal(e)
	}
	if e = m.SetConfig(p.Manifest.ID, map[string]any{"brightness": float64(73)}); e != nil {
		t.Fatal(e)
	}
	legacy := clone(m.state)
	legacy.Sources = nil
	legacy.Plugins[p.Manifest.ID].SourceID = ""
	legacy.Plugins[p.Manifest.ID].SourceName = ""
	legacy.Plugins[p.Manifest.ID].SourceOfficial = false
	if e = atomicJSON(filepath.Join(m.dir, "state.json"), legacy); e != nil {
		t.Fatal(e)
	}
	loaded, e := NewManager(m.dir, false, true)
	if e != nil {
		t.Fatal(e)
	}
	defer loaded.Ring.Close()
	if len(loaded.state.Sources) != 1 || loaded.state.Sources[0].URL != fixture.Server.URL+"/catalog/v1.json" {
		t.Fatal("lost migrated source")
	}
	installed := loaded.state.Plugins[p.Manifest.ID]
	if installed.Config["brightness"] != float64(73) || installed.SourceID != loaded.state.Sources[0].ID {
		t.Fatal("configuration or provenance lost during migration", installed)
	}
	system := loaded.System()
	disk := system["disk"].(DiskUsage)
	if !disk.Available || disk.TotalBytes == 0 || disk.AvailableBytes > disk.TotalBytes || disk.ReserveBytes != StorageReserve {
		t.Fatal("incorrect disk statistics", disk)
	}
}
