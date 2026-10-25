package homebrew

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func leb(n uint32) []byte {
	var b []byte
	for {
		v := byte(n & 127)
		n >>= 7
		if n != 0 {
			v |= 128
		}
		b = append(b, v)
		if n == 0 {
			return b
		}
	}
}
func section(id byte, payload []byte) []byte {
	return append(append([]byte{id}, leb(uint32(len(payload)))...), payload...)
}
func wasmString(s string) []byte { return append(leb(uint32(len(s))), []byte(s)...) }
func minimalWasm(loop bool) []byte {
	b := []byte{0, 97, 115, 109, 1, 0, 0, 0}
	b = append(b, section(1, []byte{1, 0x60, 1, 0x7f, 0})...)
	b = append(b, section(3, []byte{1, 0})...)
	b = append(b, section(5, []byte{1, 1, 1, 32})...)
	exports := []byte{2}
	exports = append(exports, wasmString("memory")...)
	exports = append(exports, 2, 0)
	exports = append(exports, wasmString("alice_tick")...)
	exports = append(exports, 0, 0)
	b = append(b, section(7, exports)...)
	body := []byte{0, 0x0b}
	if loop {
		body = []byte{0, 3, 0x40, 0x0c, 0, 0x0b, 0x0b}
	}
	code := append([]byte{1}, leb(uint32(len(body)))...)
	code = append(code, body...)
	return append(b, section(10, code)...)
}
func ledWasm(ptr uint32) []byte {
	b := []byte{0, 97, 115, 109, 1, 0, 0, 0}
	b = append(b, section(1, []byte{2, 0x60, 2, 0x7f, 0x7f, 1, 0x7f, 0x60, 1, 0x7f, 0})...)
	imports := []byte{1}
	imports = append(imports, wasmString("alice")...)
	imports = append(imports, wasmString("led_write")...)
	imports = append(imports, 0, 0)
	b = append(b, section(2, imports)...)
	b = append(b, section(3, []byte{1, 1})...)
	b = append(b, section(5, []byte{1, 1, 1, 32})...)
	exports := []byte{2}
	exports = append(exports, wasmString("memory")...)
	exports = append(exports, 2, 0)
	exports = append(exports, wasmString("alice_tick")...)
	exports = append(exports, 0, 1)
	b = append(b, section(7, exports)...)
	body := append([]byte{0, 0x41}, leb(ptr)...)
	body = append(body, 0x41, 36, 0x10, 0, 0x1a, 0x0b)
	code := append([]byte{1}, leb(uint32(len(body)))...)
	code = append(code, body...)
	return append(b, section(10, code)...)
}
func manifest(version string) Manifest {
	return Manifest{Format: 1, ID: "alice.test", Version: version, API: 1, Title: Text{RU: "Тест", EN: "Test"}, Author: "Test", Permissions: []string{}, Config: []Field{{Key: "brightness", Label: Text{EN: "Brightness"}, Type: "range", Default: float64(25), Min: 0, Max: 100, Step: 1}}}
}
func packageBytes(t *testing.T, m Manifest, module []byte, extra string) []byte {
	t.Helper()
	m.ModuleSHA256 = Digest(module)
	raw, _ := json.Marshal(m)
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for _, v := range []struct {
		name string
		b    []byte
	}{{"manifest.json", raw}, {"plugin.wasm", module}} {
		w, e := z.Create(v.name)
		if e != nil {
			t.Fatal(e)
		}
		w.Write(v.b)
	}
	if extra != "" {
		w, _ := z.Create(extra)
		w.Write([]byte("bad"))
	}
	if e := z.Close(); e != nil {
		t.Fatal(e)
	}
	return b.Bytes()
}
func mustPackage(t *testing.T, version string) *Package {
	t.Helper()
	p, e := Parse(packageBytes(t, manifest(version), minimalWasm(false), ""))
	if e != nil {
		t.Fatal(e)
	}
	return p
}
func manager(t *testing.T) *Manager {
	t.Helper()
	m, e := NewManager(t.TempDir(), false, true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		m.mu.Lock()
		for id := range m.running {
			m.stop(id)
		}
		m.mu.Unlock()
		m.Ring.Close()
	})
	return m
}
func TestPackageAndSchema(t *testing.T) {
	p := mustPackage(t, "1.0.0")
	if e := ValidateModule(p); e != nil {
		t.Fatal(e)
	}
	for _, values := range []map[string]any{{"brightness": float64(101)}, {"brightness": "high"}, {"command": "rm"}} {
		if _, e := Config(p.Manifest, values); e == nil {
			t.Fatal("invalid config accepted")
		}
	}
	if _, e := Parse(packageBytes(t, p.Manifest, p.Module, "../escape")); e == nil {
		t.Fatal("unsafe ZIP path accepted")
	}
	bad := manifest("1.0.0")
	bad.Permissions = []string{"root.exec"}
	if e := ValidateManifest(bad); e == nil {
		t.Fatal("unknown permission accepted")
	}
}
func TestHostPermissionsAndMemoryBounds(t *testing.T) {
	for _, tc := range []struct {
		permission bool
		ptr        uint32
		want       string
	}{{false, 0, "permission denied"}, {true, 65530, "outside WebAssembly memory"}} {
		m := manifest("1.0.0")
		if tc.permission {
			m.Permissions = []string{"led.ring"}
		}
		p := &Package{Manifest: m, Module: ledWasm(tc.ptr)}
		r, e := NewRunner(p, map[string]any{})
		if e != nil {
			t.Fatal(e)
		}
		_, e = r.Tick(nil, 0)
		r.Close()
		if e == nil || !strings.Contains(e.Error(), tc.want) {
			t.Fatalf("expected %s, got %v", tc.want, e)
		}
	}
}
func TestInfiniteLoopTerminates(t *testing.T) {
	p := &Package{Manifest: manifest("1.0.0"), Module: minimalWasm(true)}
	r, e := NewRunner(p, nil)
	if e != nil {
		t.Fatal(e)
	}
	defer r.Close()
	start := time.Now()
	if _, e = r.Tick(nil, 0); e == nil {
		t.Fatal("infinite loop completed")
	}
	if time.Since(start) > 2*time.Second {
		t.Fatal("plugin timeout was not enforced")
	}
}
func TestTableAndImportLimits(t *testing.T) {
	b := []byte{0, 97, 115, 109, 1, 0, 0, 0}
	payload := append([]byte{1, 0x70, 1, 1}, leb(1000000)...)
	if e := wasmLimits(append(b, section(4, payload)...)); e == nil {
		t.Fatal("huge table accepted")
	}
	p := []byte{1}
	p = append(p, wasmString("wasi_snapshot_preview1")...)
	p = append(p, wasmString("proc_exit")...)
	p = append(p, 0, 0)
	if e := wasmLimits(append(b, section(2, p)...)); e == nil {
		t.Fatal("WASI import accepted")
	}
}
func TestLocalInstallRequiresConsent(t *testing.T) {
	m := manager(t)
	p := mustPackage(t, "1.0.0")
	staged, e := m.Stage(p.Raw, false)
	if e != nil {
		t.Fatal(e)
	}
	ticket := staged["ticket"].(string)
	if e = m.Install(ticket, nil, false); e == nil {
		t.Fatal("unreviewed package installed without consent")
	}
	if e = m.Install(ticket, nil, true); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(p.Manifest.ID, true); e != nil {
		t.Fatal(e)
	}
	if e = m.SetConfig(p.Manifest.ID, map[string]any{"brightness": float64(40)}); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(p.Manifest.ID, false); e != nil {
		t.Fatal(e)
	}
	if e = m.Remove(p.Manifest.ID); e != nil {
		t.Fatal(e)
	}
}
func TestSignedReviewUpdateRollbackRevocationAndPagesExport(t *testing.T) {
	store, e := NewStore(t.TempDir(), "")
	if e != nil {
		t.Fatal(e)
	}
	server := httptest.NewServer(NewServer(nil, store, "admin"))
	defer server.Close()
	ctx := context.Background()
	m := manager(t)
	p := mustPackage(t, "1.0.0")
	submission, e := store.Submit(ctx, p.Raw)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = store.Download(p.SHA256, false); e == nil {
		t.Fatal("pending package was public")
	}
	if e = store.Review(submission.ID, "approve", "", false); e == nil {
		t.Fatal("approval without manual review accepted")
	}
	if e = store.Review(submission.ID, "approve", "tested", true); e != nil {
		t.Fatal(e)
	}
	if e = m.SetStore(server.URL+"/catalog/v1.json", store.PublicKey()); e != nil {
		t.Fatal(e)
	}
	if e = m.Refresh(ctx); e != nil {
		t.Fatal(e)
	}
	staged, e := m.StageEntry(ctx, p.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e != nil {
		t.Fatal(e)
	}
	if e = m.SetConfig(p.Manifest.ID, map[string]any{"brightness": float64(37)}); e != nil {
		t.Fatal(e)
	}
	p2 := mustPackage(t, "1.1.0")
	s2, e := store.Submit(ctx, p2.Raw)
	if e != nil {
		t.Fatal(e)
	}
	if e = store.Review(s2.ID, "approve", "tested", true); e != nil {
		t.Fatal(e)
	}
	if e = m.Refresh(ctx); e != nil {
		t.Fatal(e)
	}
	staged, e = m.StageEntry(ctx, p2.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e != nil {
		t.Fatal(e)
	}
	if m.state.Plugins[p.Manifest.ID].Config["brightness"] != float64(37) {
		t.Fatal("config not migrated")
	}
	if e = m.Rollback(p.Manifest.ID, nil); e != nil {
		t.Fatal(e)
	}
	if m.state.Plugins[p.Manifest.ID].SHA256 != p.SHA256 {
		t.Fatal("rollback failed")
	}
	if e = store.Review(submission.ID, "revoke", "withdrawn", false); e != nil {
		t.Fatal(e)
	}
	if e = m.Refresh(ctx); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(p.Manifest.ID, true); e == nil {
		t.Fatal("revoked plugin enabled")
	}
	reloaded, e := NewManager(m.dir, false, true)
	if e != nil {
		t.Fatal(e)
	}
	defer reloaded.Ring.Close()
	if e = reloaded.Enable(p.Manifest.ID, true); e == nil {
		t.Fatal("revocation was not persisted")
	}
	var site bytes.Buffer
	if e = store.Export(&site, "https://github.com/example/alice-store"); e != nil {
		t.Fatal(e)
	}
	z, e := zip.NewReader(bytes.NewReader(site.Bytes()), int64(site.Len()))
	if e != nil {
		t.Fatal(e)
	}
	files := map[string]bool{}
	for _, f := range z.File {
		files[f.Name] = true
		if strings.Contains(f.Name, ".key") || strings.Contains(f.Name, "admin-token") {
			t.Fatal("secret in export")
		}
		if f.Name == "docs/catalog/v1.json" {
			r, _ := f.Open()
			raw, _ := io.ReadAll(r)
			r.Close()
			if _, e = VerifyCatalog(raw, store.PublicKey(), 2); e != nil {
				t.Fatal(e)
			}
		}
	}
	if !files["docs/index.html"] || !files[".github/ISSUE_TEMPLATE/plugin.yml"] || !files["docs/packages/"+p2.SHA256+".aliceplugin"] || files["docs/packages/"+p.SHA256+".aliceplugin"] {
		t.Fatal("invalid exported site contents")
	}
}
func TestPinnedKeyAndCatalogRollback(t *testing.T) {
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	otherPub, _, _ := ed25519.GenerateKey(rand.Reader)
	c := Catalog{Format: 1, Revision: 2, Generated: time.Now(), Expires: time.Now().Add(time.Hour), Entries: []Entry{}, Revoked: []string{}}
	v, _ := SignCatalog(c, key)
	raw, _ := json.Marshal(v)
	if _, e := VerifyCatalog(raw, base64.StdEncoding.EncodeToString(otherPub), 0); e == nil {
		t.Fatal("wrong moderator key accepted")
	}
	if _, e := VerifyCatalog(raw, base64.StdEncoding.EncodeToString(pub), 3); e == nil {
		t.Fatal("older revision accepted")
	}
	if _, e := VerifyCatalog(raw, base64.StdEncoding.EncodeToString(pub), 2); e != nil {
		t.Fatal(e)
	}
}
func TestAuthenticationAndOriginChecks(t *testing.T) {
	m := manager(t)
	s := NewServer(m, nil, "correct-token")
	req := httptest.NewRequest("GET", "/api/state", nil)
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != 401 {
		t.Fatal("unauthenticated state exposed")
	}
	req = httptest.NewRequest("POST", "/api/login", strings.NewReader(`{"token":"correct-token"}`))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	cookie := w.Result().Cookies()[0]
	if !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode {
		t.Fatal("weak session cookie")
	}
	req = httptest.NewRequest("POST", "/api/settings", strings.NewReader(`{"catalog_url":"","catalog_key":""}`))
	req.AddCookie(cookie)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://attacker.example")
	w = httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != 403 {
		t.Fatal("cross-origin mutation accepted")
	}
}

func TestStationWithoutAccessCode(t *testing.T) {
	s := NewServer(manager(t), nil, "")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("GET", "/api/state", nil))
	if w.Code != 200 {
		t.Fatal("station requested an access code", w.Body.String())
	}
	w = httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("GET", "/api/about", nil))
	var about map[string]any
	if e := json.Unmarshal(w.Body.Bytes(), &about); e != nil || about["access_code_required"] != false || about["authenticated"] != true {
		t.Fatal("station did not advertise direct access", w.Body.String())
	}
	req := httptest.NewRequest("POST", "/api/settings", strings.NewReader(`{"catalog_url":"","catalog_key":""}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://attacker.example")
	w = httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != 403 {
		t.Fatal("cross-origin mutation accepted without an access code")
	}
}

func TestStationFetchesLocalPackageWithoutClaimingStoreReview(t *testing.T) {
	p := mustPackage(t, "1.0.0")
	requests := 0
	host := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Header.Get("Authorization") != "" || len(r.Cookies()) != 0 {
			t.Error("credentials leaked to download server")
		}
		w.Write(p.Raw)
	}))
	defer host.Close()
	m := manager(t)
	m.AllowLocalStore = false
	staged, e := m.FetchURL(context.Background(), host.URL+"/MyPlugin.aliceplugin", p.SHA256)
	if e != nil {
		t.Fatal(e)
	}
	if requests != 1 || staged["reviewed"] != false {
		t.Fatal("manual fetch incorrectly claimed moderator review")
	}
	if e = m.Install(staged["ticket"].(string), nil, false); e == nil {
		t.Fatal("manual fetch installed without explicit local consent")
	}
	if e = m.Install(staged["ticket"].(string), nil, true); e != nil {
		t.Fatal(e)
	}
	if _, e = m.FetchURL(context.Background(), host.URL, strings.Repeat("0", 64)); e == nil {
		t.Fatal("incorrect expected checksum accepted")
	}
	for _, raw := range []string{"file:///etc/passwd", "ftp://example.com/plugin.aliceplugin", "http://169.254.169.254/plugin.aliceplugin", "https://user:password@example.com/plugin.aliceplugin"} {
		if _, e = manualURL(raw, false); e == nil {
			t.Fatalf("unsafe URL accepted: %s", raw)
		}
	}
}
