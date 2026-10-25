package homebrew

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func nativeFixture(t *testing.T) *Package {
	t.Helper()
	b := make([]byte, 132)
	copy(b, []byte{0x7f, 'E', 'L', 'F', 2, 1, 1})
	put := binary.LittleEndian
	put.PutUint16(b[16:], 2)
	put.PutUint16(b[18:], 183)
	put.PutUint32(b[20:], 1)
	put.PutUint64(b[24:], 0x400078)
	put.PutUint64(b[32:], 64)
	put.PutUint16(b[52:], 64)
	put.PutUint16(b[54:], 56)
	put.PutUint16(b[56:], 1)
	put.PutUint32(b[64:], 1)
	put.PutUint32(b[68:], 5)
	put.PutUint64(b[80:], 0x400000)
	put.PutUint64(b[88:], 0x400000)
	put.PutUint64(b[96:], 132)
	put.PutUint64(b[104:], 132)
	put.PutUint64(b[112:], 4096)
	copy(b[120:], []byte{0, 0, 0x80, 0xd2, 0xa8, 0x0b, 0x80, 0xd2, 1, 0, 0, 0xd4})
	m := Manifest{Format: 1, ID: "native-test", Version: "1.0.0", API: 2, Engine: "native", Title: Text{EN: "Test"}, Permissions: []string{"system.full-access"}, ModuleSHA256: Digest(b), RuntimeStorage: 1024}
	var out bytes.Buffer
	z := zip.NewWriter(&out)
	manifest, _ := json.Marshal(m)
	for n, d := range map[string][]byte{"manifest.json": manifest, "plugin.elf": b} {
		w, _ := z.Create(n)
		w.Write(d)
	}
	z.Close()
	p, e := Parse(out.Bytes())
	if e != nil {
		t.Fatal(e)
	}
	return p
}
func TestNativeInspectionNeverRuns(t *testing.T) {
	p := nativeFixture(t)
	if e := ValidateModule(p); e != nil {
		t.Fatal(e)
	}
	if e := ValidateModuleFormat(p); e != nil {
		t.Fatal(e)
	}
	m := manager(t)
	staged, e := m.Stage(p.Raw, false)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(filepath.Join(m.dir, "plugin-data")); !os.IsNotExist(e) {
		t.Fatal("inspection extracted executable")
	}
	id := staged["ticket"].(string)
	if e = m.Install(id, nil, true); e == nil {
		t.Fatal("native permission not required")
	}
	if e = m.Install(id, p.Manifest.Permissions, true); e != nil {
		t.Fatal(e)
	}
	if !m.state.Plugins[p.Manifest.ID].NativeConsent {
		t.Fatal("consent not saved")
	}
	if e = m.Enable(p.Manifest.ID, true); e == nil {
		t.Fatal("enable omitted explicit native acknowledgement")
	}
	if p.RequiredStorage() != uint64(len(p.Raw)+len(p.Module))+1024 {
		t.Fatal("native disk estimate")
	}
	if len(m.running) != 0 {
		t.Fatal("inspection or installation started native plugin")
	}
}
func TestNativeRejectsWrongArchitecture(t *testing.T) {
	p := nativeFixture(t)
	b := append([]byte{}, p.Module...)
	binary.LittleEndian.PutUint16(b[18:], 62)
	if validateELF(b) == nil {
		t.Fatal("x86 ELF accepted")
	}
	b = append([]byte{}, p.Module...)
	binary.LittleEndian.PutUint64(b[24:], 1)
	if validateELF(b) == nil {
		t.Fatal("unmapped entry accepted")
	}
	p.Manifest.Permissions = nil
	if ValidateManifest(p.Manifest) == nil {
		t.Fatal("full-root declaration optional")
	}
}
func TestCatalogV1KeepsNativeOut(t *testing.T) {
	p := nativeFixture(t)
	s, e := NewStore(t.TempDir(), "")
	if e != nil {
		t.Fatal(e)
	}
	_, key, _ := ed25519.GenerateKey(rand.Reader)
	s.key = key
	s.state.Revision = 3
	s.state.Submissions["native"] = &Submission{Manifest: p.Manifest, SHA256: p.SHA256, Bytes: len(p.Raw), Status: "approved", Reviewed: time.Now()}
	for _, api := range []int{1, 2} {
		env, e := s.CatalogFor(api)
		if e != nil {
			t.Fatal(e)
		}
		b, _ := json.Marshal(env)
		c, e := VerifyCatalog(b, s.PublicKey(), 3)
		if e != nil {
			t.Fatal(e)
		}
		if len(c.Entries) != api-1 {
			t.Fatalf("API %d: %d", api, len(c.Entries))
		}
	}
}
