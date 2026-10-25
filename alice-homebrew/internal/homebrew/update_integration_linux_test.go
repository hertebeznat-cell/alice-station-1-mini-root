//go:build linux

package homebrew

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Optional real-process fixture, built and run locally before publishing. No
// speaker is involved; the only network is mocked GitHub and a loopback server.
func TestManagerSelfUpdateIntegration(t *testing.T) {
	oldBinary := os.Getenv("ALICE_UPDATE_TEST_OLD")
	nextBinary := os.Getenv("ALICE_UPDATE_TEST_NEXT")
	if oldBinary == "" || nextBinary == "" {
		t.Skip("set old/next test binary paths to run real-process update fixture")
	}
	for _, tc := range []struct{ name, path, version, state string }{{"upgrade", nextBinary, "0.4.1", "updated"}, {"rollback", os.Getenv("ALICE_UPDATE_TEST_BAD"), Version, "rolled-back"}} {
		if tc.path == "" {
			continue
		}
		t.Run(tc.name, func(t *testing.T) { runManagerUpdateFixture(t, oldBinary, tc.path, tc.version, tc.state) })
	}
}
func runManagerUpdateFixture(t *testing.T, oldBinary, nextBinary, expectedVersion, expectedState string) {
	m := manager(t)
	pkg := mustPackage(t, "1.0.0")
	staged, e := m.Stage(pkg.Raw, false)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.Install(staged["ticket"].(string), nil, true); e != nil {
		t.Fatal(e)
	}
	if e = m.SetConfig(pkg.Manifest.ID, map[string]any{"brightness": float64(73)}); e != nil {
		t.Fatal(e)
	}
	if e = m.Enable(pkg.Manifest.ID, true); e != nil {
		t.Fatal(e)
	}
	m.stop(pkg.Manifest.ID)
	if e = m.ChangeSource("official", false, false); e != nil {
		t.Fatal(e)
	}
	root := t.TempDir()
	target := filepath.Join(root, "alice-homebrew")
	raw, e := os.ReadFile(oldBinary)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(target, raw, 0700); e != nil {
		t.Fatal(e)
	}
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	listen := listener.Addr().String()
	listener.Close()
	args := []string{"station", "--listen", listen, "--data", m.dir, "--managed"}
	// The launched service must not contact the real GitHub during this fixture.
	initial := UpdateStatus{Enabled: true, Supported: true, Current: Version, State: "idle", NextCheck: time.Now().Add(UpdateInterval), IntervalHours: 5}
	atomicJSON(filepath.Join(m.dir, "updates.json"), initial)
	log, _ := os.Create(filepath.Join(root, "launch.log"))
	defer log.Close()
	cmd := exec.Command(target, args...)
	cmd.Stdout = log
	cmd.Stderr = log
	if e = cmd.Start(); e != nil {
		t.Fatal(e)
	}
	atomicFile(filepath.Join(root, "service.pid"), []byte(strconv.Itoa(cmd.Process.Pid)))
	defer func() {
		b, _ := os.ReadFile(filepath.Join(root, "service.pid"))
		pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
		if pid > 1 {
			syscall.Kill(pid, syscall.SIGTERM)
		}
		cmd.Wait()
	}()
	health := "http://" + listen + "/api/about"
	waitVersion := func(version string) bool {
		deadline := time.Now().Add(35 * time.Second)
		for time.Now().Before(deadline) {
			response, err := http.Get(health)
			if err == nil {
				var about struct {
					Version string `json:"version"`
					Ready   bool   `json:"ready"`
				}
				json.NewDecoder(response.Body).Decode(&about)
				response.Body.Close()
				if about.Version == version && about.Ready {
					return true
				}
			}
			time.Sleep(100 * time.Millisecond)
		}
		return false
	}
	if !waitVersion(Version) {
		t.Fatal("fixture service did not start")
	}
	candidate, e := os.ReadFile(nextBinary)
	if e != nil {
		t.Fatal(e)
	}
	asset := releaseAsset{Name: "alice-homebrew-linux-" + runtime.GOARCH, URL: fmt.Sprintf("https://github.com/%s/releases/download/v0.4.1/alice-homebrew-linux-%s", updateRepository, runtime.GOARCH), Digest: "sha256:" + Digest(candidate), Size: int64(len(candidate)), State: "uploaded"}
	release := managerRelease{Tag: "v0.4.1", Assets: []releaseAsset{asset}}
	u, e := NewUpdater(m.dir, listen, false)
	if e != nil {
		t.Fatal(e)
	}
	u.executable = target
	u.args = args
	u.pid = cmd.Process.Pid
	u.status.Supported = true
	u.client = &http.Client{Transport: updateTransport(func(r *http.Request) (*http.Response, error) {
		var b []byte
		if r.URL.String() == releaseAPI {
			b, _ = json.Marshal(release)
		} else if r.URL.String() == asset.URL {
			b = candidate
		} else {
			t.Fatal("unexpected request", r.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(b))}, nil
	})}
	status, e := u.Check(context.Background(), true)
	if e != nil {
		t.Fatal(e)
	}
	if status.State != "restarting" {
		t.Fatal(status)
	}
	if expectedState == "updated" && !waitVersion(expectedVersion) {
		b, _ := os.ReadFile(filepath.Join(root, "update-helper.log"))
		t.Fatalf("new version did not become ready: %s", b)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		b, _ := os.ReadFile(filepath.Join(m.dir, "updates.json"))
		var final UpdateStatus
		_ = json.Unmarshal(b, &final)
		if final.State == expectedState {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !waitVersion(expectedVersion) {
		t.Fatal("expected manager version is not healthy after transaction")
	}
	response, e := http.Get("http://" + listen + "/api/state")
	if e != nil {
		t.Fatal(e)
	}
	defer response.Body.Close()
	var snapshot struct {
		Plugins []Installed  `json:"plugins"`
		Updates UpdateStatus `json:"updates"`
	}
	if e = json.NewDecoder(response.Body).Decode(&snapshot); e != nil {
		t.Fatal(e)
	}
	if len(snapshot.Plugins) != 1 || snapshot.Plugins[0].Config["brightness"] != float64(73) || snapshot.Plugins[0].SHA256 != pkg.SHA256 || !snapshot.Plugins[0].Enabled {
		t.Fatal("plugin/config changed", snapshot.Plugins)
	}
	if snapshot.Updates.State != expectedState || snapshot.Updates.Current != expectedVersion || snapshot.Updates.NextCheck.Before(time.Now().Add(4*time.Hour)) {
		t.Fatal("update status/schedule incorrect", snapshot.Updates)
	}
	backup := target + ".previous"
	if expectedState == "rolled-back" {
		backup = target
	}
	previous, e := fileDigest(backup)
	if e != nil || previous != Digest(raw) {
		t.Fatal("rollback binary not preserved", e)
	}
	t.Log("real-process upgrade, health check, plugin/config preservation, backup and next 5-hour check passed")
}
