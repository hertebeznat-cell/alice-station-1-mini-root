//go:build linux

package homebrew

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func rollbackFixture(t *testing.T) (UpdatePlan, string) {
	t.Helper()
	root := t.TempDir()
	data := filepath.Join(root, "state")
	os.Mkdir(data, 0700)
	old, next, state := []byte("old manager"), []byte("new manager"), []byte(`{"plugins":{}}`)
	p := UpdatePlan{Target: filepath.Join(root, "alice-homebrew"), Data: data, Args: []string{"station", "--managed", "--data", data}, PID: 999999, ProcessStart: "not-a-running-process", Version: "0.4.1", PreviousVersion: "0.4.0", SHA256: Digest(next), PreviousSHA256: Digest(old), StateSHA256: Digest(state), HealthURL: "http://127.0.0.1:18099/api/about", Phase: "testing", Created: time.Now()}
	os.WriteFile(p.Target, next, 0700)
	os.WriteFile(p.Target+".previous", old, 0700)
	os.WriteFile(filepath.Join(data, "state-before-manager-update.json"), state, 0600)
	os.WriteFile(filepath.Join(data, "state.json"), []byte("partially migrated state"), 0600)
	path := filepath.Join(data, "manager-update.json")
	if e := atomicJSON(path, p); e != nil {
		t.Fatal(e)
	}
	return p, path
}
func TestRecoverInterruptedManagerAndState(t *testing.T) {
	p, path := rollbackFixture(t)
	if e := RecoverManagerUpdate(path); e != nil {
		t.Fatal(e)
	}
	actual, _ := fileDigest(p.Target)
	if actual != p.PreviousSHA256 {
		t.Fatal("old manager not restored")
	}
	state, _ := fileDigest(filepath.Join(p.Data, "state.json"))
	if state != p.StateSHA256 {
		t.Fatal("configuration not restored")
	}
	if _, e := os.Stat(path); !os.IsNotExist(e) {
		t.Fatal("journal retained after recovery")
	}
	var status UpdateStatus
	b, _ := os.ReadFile(filepath.Join(p.Data, "updates.json"))
	if decode(b, &status) != nil || status.FailedVersion != p.Version || status.State != "rolled-back" {
		t.Fatal(string(b))
	}
}
func TestRollbackRefusesChangedBackup(t *testing.T) {
	p, _ := rollbackFixture(t)
	os.WriteFile(p.Target+".previous", []byte("unrelated file"), 0700)
	if e := restoreManager(p); e == nil {
		t.Fatal("changed backup accepted")
	}
	b, _ := os.ReadFile(p.Target)
	if string(b) != "new manager" {
		t.Fatal("current manager overwritten")
	}
}
func TestPreparedRecoveryKeepsCurrentState(t *testing.T) {
	p, path := rollbackFixture(t)
	p.Phase = "prepared"
	p.StateSHA256 = ""
	os.WriteFile(p.Target, []byte("old manager"), 0700)
	atomicJSON(path, p)
	before, _ := os.ReadFile(filepath.Join(p.Data, "state.json"))
	if e := RecoverManagerUpdate(path); e != nil {
		t.Fatal(e)
	}
	after, _ := os.ReadFile(filepath.Join(p.Data, "state.json"))
	if string(before) != string(after) {
		t.Fatal("a stale config backup was applied before swapping")
	}
}
