//go:build uitest

package homebrew

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// An explicitly requested, loopback-only browser fixture. It is never compiled
// into the application and owns only temporary test plugin/config data.
func TestUIFixture(t *testing.T) {
	metadata := os.Getenv("ALICE_HOMEBREW_UI_METADATA")
	if metadata == "" {
		t.Skip("set ALICE_HOMEBREW_UI_METADATA to run the browser fixture")
	}
	m := manager(t)
	makePackage := func(id string, padding int) *Package {
		manifest := manifest("1.0.0")
		manifest.ID = id
		manifest.Title = Text{RU: map[string]string{"fixture.large": "Большой тестовый плагин", "fixture.small": "Маленький тестовый плагин", "fixture.new": "Новый тестовый плагин"}[id], EN: id}
		manifest.ModuleSHA256 = Digest(minimalWasm(false))
		encoded, _ := json.Marshal(manifest)
		var buffer bytes.Buffer
		archive := zip.NewWriter(&buffer)
		archive.SetComment(strings.Repeat("x", padding))
		for _, item := range []struct {
			Name string
			Data []byte
		}{{"manifest.json", encoded}, {"plugin.wasm", minimalWasm(false)}} {
			w, _ := archive.Create(item.Name)
			w.Write(item.Data)
		}
		archive.Close()
		p, e := Parse(buffer.Bytes())
		if e != nil {
			t.Fatal(e)
		}
		return p
	}
	large, small, next := makePackage("fixture.large", 12000), makePackage("fixture.small", 1500), makePackage("fixture.new", 6000)
	for _, p := range []*Package{large, small} {
		staged, e := m.Stage(p.Raw, false)
		if e != nil {
			t.Fatal(e)
		}
		if e = m.Install(staged["ticket"].(string), nil, true); e != nil {
			t.Fatal(e)
		}
	}
	initial := uint64(len(large.Raw) + len(small.Raw))
	m.diskProbe = func(string) DiskUsage {
		var used uint64
		for _, p := range m.state.Plugins {
			size, _ := m.pluginBytesLocked(p)
			used += size
		}
		free := StorageReserve - 100
		if used < initial {
			free += initial - used
		}
		return DiskUsage{Available: true, AvailableBytes: free, TotalBytes: 96 << 20, UsedBytes: (96 << 20) - free}
	}
	fixture := sourceFixture(t, next)
	handler := NewServer(m, nil, "")
	done := make(chan struct{}, 1)
	mux := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/fixture/package.aliceplugin" {
			w.Write(next.Raw)
			return
		}
		if r.URL.Path == "/fixture/done" && r.Method == "POST" {
			done <- struct{}{}
			w.Write([]byte("done"))
			return
		}
		handler.ServeHTTP(w, r)
	})
	listener, e := net.Listen("tcp", "127.0.0.1:18094")
	if e != nil {
		t.Fatal(e)
	}
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go server.Serve(listener)
	defer server.Close()
	payload := map[string]string{"manager": "http://127.0.0.1:18094", "source": fixture.Server.URL, "package": "http://127.0.0.1:18094/fixture/package.aliceplugin"}
	if e = atomicJSON(filepath.Clean(metadata), payload); e != nil {
		t.Fatal(e)
	}
	fmt.Println("Loopback UI fixture ready; temporary plugins only.")
	select {
	case <-done:
	case <-time.After(15 * time.Minute):
		t.Fatal("UI fixture timed out")
	}
	server.Shutdown(context.Background())
}
