package homebrew

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func cacheFixture(t *testing.T, m *Manager) (string, *Package, *Package) {
	t.Helper()
	current, previous := mustPackage(t, "2.0.0"), mustPackage(t, "1.0.0")
	for _, pkg := range []*Package{current, previous} {
		if err := putPackage(filepath.Join(m.dir, "packages"), pkg); err != nil {
			t.Fatal(err)
		}
	}
	m.state.Plugins[current.Manifest.ID] = &Installed{Saved: Saved{Manifest: current.Manifest, SHA256: current.SHA256, Config: map[string]any{"brightness": float64(42)}}, Previous: &Saved{Manifest: previous.Manifest, SHA256: previous.SHA256, Config: map[string]any{"brightness": float64(17)}}}
	data := filepath.Join(m.dir, "plugin-data", current.Manifest.ID)
	for _, hash := range []string{current.SHA256, previous.SHA256, strings.Repeat("a", 64)} {
		path := filepath.Join(data, "runtimes", hash)
		if err := os.MkdirAll(path, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(path, "plugin.elf"), make([]byte, 2048), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"config.json", "shairport-sync", "user-data/keep.txt", "runtimes/custom/keep.txt"} {
		path := filepath.Join(data, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("keep"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return data, current, previous
}

func TestRuntimeCacheCleanupPreservesDataAndRollback(t *testing.T) {
	m := manager(t)
	data, current, previous := cacheFixture(t, m)
	before := directoryBytes(data)
	m.gc()
	if freed := before - directoryBytes(data); freed != 4096 {
		t.Fatalf("freed %d, want 4096", freed)
	}
	for _, name := range []string{"config.json", "shairport-sync", "user-data/keep.txt", "runtimes/custom/keep.txt", "runtimes/" + current.SHA256 + "/plugin.elf"} {
		if _, err := os.Stat(filepath.Join(data, name)); err != nil {
			t.Fatalf("retained file %s: %v", name, err)
		}
	}
	if _, err := m.load(previous.SHA256); err != nil {
		t.Fatal("rollback package removed:", err)
	}
	if err := m.Rollback(current.Manifest.ID, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(data, "runtimes", current.SHA256)); !os.IsNotExist(err) {
		t.Fatalf("old current runtime retained: %v", err)
	}
	if err := m.Enable(current.Manifest.ID, true); err != nil {
		t.Fatal("rollback could not restart from package:", err)
	}
	if got := m.state.Plugins[current.Manifest.ID].Config["brightness"]; got != float64(17) {
		t.Fatalf("rollback config changed: %v", got)
	}
}

func TestRuntimeCacheCleanupOnManagerStartup(t *testing.T) {
	m := manager(t)
	data, current, previous := cacheFixture(t, m)
	if err := m.save(); err != nil {
		t.Fatal(err)
	}
	reloaded, err := NewManager(m.dir, false, true)
	if err != nil {
		t.Fatal(err)
	}
	defer reloaded.Ring.Close()
	if _, err := os.Stat(filepath.Join(data, "runtimes", previous.SHA256)); !os.IsNotExist(err) {
		t.Fatalf("old runtime not collected at startup: %v", err)
	}
	if _, err := os.Stat(filepath.Join(data, "runtimes", current.SHA256)); err != nil {
		t.Fatal(err)
	}
	if err := cleanRuntimeCache(filepath.Join(m.dir, "plugin-data"), current.Manifest.ID, current.SHA256); err != nil {
		t.Fatal("second cleanup must be safe:", err)
	}
}

func TestRuntimeCacheCleanupAfterPluginUpdate(t *testing.T) {
	m := manager(t)
	data, current, _ := cacheFixture(t, m)
	old := m.state.Plugins[current.Manifest.ID]
	old.Reviewed, old.SourceID, old.Enabled = true, "official", true
	if err := m.start(current.Manifest.ID); err != nil {
		t.Fatal(err)
	}
	next := mustPackage(t, "3.0.0")
	staged, err := m.Stage(next.Raw, true)
	if err != nil {
		t.Fatal(err)
	}
	ticketID := staged["ticket"].(string)
	ticket := m.tickets[ticketID]
	ticket.SourceID = "official"
	m.tickets[ticketID] = ticket
	m.catalog = &Catalog{Entries: []Entry{{Manifest: next.Manifest, SHA256: next.SHA256, SourceID: "official"}}}
	m.catalogs["official"] = &Catalog{Expires: time.Now().Add(time.Hour)}
	if err := m.InstallUpdate(ticketID, current.Manifest.ID, current.SHA256, nil, false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(data, "runtimes", current.SHA256)); !os.IsNotExist(err) {
		t.Fatalf("old runtime survived update: %v", err)
	}
	updated := m.state.Plugins[current.Manifest.ID]
	if !updated.Enabled || m.running[current.Manifest.ID] == nil || updated.Config["brightness"] != float64(42) {
		t.Fatal("update lost active state or configuration")
	}
	if updated.Previous == nil || updated.Previous.SHA256 != current.SHA256 {
		t.Fatal("update lost rollback record")
	}
	if _, err := m.load(current.SHA256); err != nil {
		t.Fatal("update lost rollback package:", err)
	}
}

func TestRuntimeCacheCleanupRejectsPathEscape(t *testing.T) {
	for _, id := range []string{"../outside", "..", "airplay/../../outside"} {
		if err := cleanRuntimeCache(t.TempDir(), id, strings.Repeat("a", 64)); err == nil {
			t.Fatalf("accepted invalid ID %q", id)
		}
	}
}

func TestRuntimeCacheCleanupDoesNotFollowSymlinks(t *testing.T) {
	for _, level := range []string{"data", "plugin", "runtimes", "entry"} {
		t.Run(level, func(t *testing.T) {
			base, outside := t.TempDir(), t.TempDir()
			current, old := strings.Repeat("a", 64), strings.Repeat("b", 64)
			root := filepath.Join(base, "plugin-data")
			paths := map[string]string{"data": root, "plugin": filepath.Join(root, "airplay"), "runtimes": filepath.Join(root, "airplay", "runtimes"), "entry": filepath.Join(root, "airplay", "runtimes", old)}
			link := paths[level]
			if err := os.MkdirAll(filepath.Dir(link), 0700); err != nil {
				t.Fatal(err)
			}
			sentinel := filepath.Join(outside, old, "keep.txt")
			if err := os.MkdirAll(filepath.Dir(sentinel), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(sentinel, []byte("keep"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, link); err != nil {
				t.Skip("symlink creation unavailable:", err)
			}
			if err := cleanRuntimeCache(root, "airplay", current); level != "entry" && err == nil {
				t.Fatalf("accepted symlink in %s path", level)
			}
			if b, err := os.ReadFile(sentinel); err != nil || string(b) != "keep" {
				t.Fatalf("followed %s symlink: %v", level, err)
			}
		})
	}
}
