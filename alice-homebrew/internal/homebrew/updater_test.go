package homebrew

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type updateTransport func(*http.Request) (*http.Response, error)

func (f updateTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func updaterFixture(t *testing.T) (*Updater, *managerRelease) {
	t.Helper()
	u, e := NewUpdater(t.TempDir(), ":8088", false)
	if e != nil {
		t.Fatal(e)
	}
	release := &managerRelease{Tag: "v" + Version}
	u.now = func() time.Time { return time.Date(2026, 10, 25, 12, 0, 0, 0, time.UTC) }
	u.client = &http.Client{Transport: updateTransport(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() != releaseAPI {
			t.Fatal("unexpected external request", r.URL)
		}
		b, _ := json.Marshal(release)
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(b))}, nil
	})}
	return u, release
}
func TestUpdateVersionOrdering(t *testing.T) {
	for _, c := range []struct {
		a, b  string
		newer bool
	}{{"0.10.0", "0.9.9", true}, {"1.0.0", "0.99.99", true}, {"0.4.0", "0.4.0", false}, {"0.3.0", "0.4.0", false}, {"0.5.0-beta.1", "0.4.0", false}, {"00.5.0", "0.4.0", false}, {"v0.4.1", "0.4.0", true}} {
		if newerVersion(c.a, c.b) != c.newer {
			t.Fatal(c)
		}
	}
}
func TestUpdateSchedulePersistsFiveHours(t *testing.T) {
	u, _ := updaterFixture(t)
	s, e := u.Check(context.Background(), false)
	if e != nil {
		t.Fatal(e)
	}
	if s.State != "up-to-date" || s.NextCheck.Sub(s.LastCheck) != 5*time.Hour {
		t.Fatal(s)
	}
	next := s.NextCheck
	restored, e := NewUpdater(u.dir, ":8088", false)
	if e != nil {
		t.Fatal(e)
	}
	if !restored.Status().NextCheck.Equal(next) {
		t.Fatal("schedule lost on restart")
	}
	if e = u.SetEnabled(false); e != nil {
		t.Fatal(e)
	}
	u.client = &http.Client{Transport: updateTransport(func(*http.Request) (*http.Response, error) {
		t.Fatal("disabled updater fetched releases")
		return nil, nil
	})}
	s, e = u.Check(context.Background(), false)
	if e != nil || s.Enabled {
		t.Fatal(s, e)
	}
}
func TestUpdaterRejectsPrereleaseAndReportsNewVersion(t *testing.T) {
	u, r := updaterFixture(t)
	r.Tag = "v99.0.0"
	r.Prerelease = true
	if _, e := u.Check(context.Background(), true); e == nil {
		t.Fatal("prerelease accepted")
	}
	r.Prerelease = false
	s, e := u.Check(context.Background(), true)
	if e != nil || s.State != "available" {
		t.Fatal(s, e)
	}
	if _, e = os.Stat(filepath.Join(u.dir, "manager-update.json")); !os.IsNotExist(e) {
		t.Fatal("unmanaged preview prepared installation")
	}
}
func TestUpdaterPinsRepositoryDigestArchitecture(t *testing.T) {
	a := releaseAsset{Name: "alice-homebrew-linux-arm64", URL: "https://github.com/" + updateRepository + "/releases/download/v0.4.1/alice-homebrew-linux-arm64", Digest: "sha256:" + strings.Repeat("a", 64), Size: 9000000, State: "uploaded"}
	r := managerRelease{Tag: "v0.4.1", Assets: []releaseAsset{a}}
	if _, e := selectUpdateAsset(r, "arm64"); e != nil {
		t.Fatal(e)
	}
	r.Assets[0].URL = strings.Replace(a.URL, updateRepository, "unrelated/manager", 1)
	if _, e := selectUpdateAsset(r, "arm64"); e == nil {
		t.Fatal("foreign repository accepted")
	}
	r.Assets[0] = a
	r.Assets[0].Digest = ""
	if _, e := selectUpdateAsset(r, "arm64"); e == nil {
		t.Fatal("missing digest accepted")
	}
	if _, e := selectUpdateAsset(r, "amd64"); e == nil {
		t.Fatal("wrong architecture accepted")
	}
	for _, raw := range []string{"http://github.com/a", "https://github.com.evil.example/a", "https://user@github.com/a", "https://github.com:8443/a", "https://example.org/a"} {
		u, _ := url.Parse(raw)
		if trustedUpdateURL(u) {
			t.Fatal(raw)
		}
	}
}
func TestUpdaterDownloadRejectsTampering(t *testing.T) {
	u, _ := updaterFixture(t)
	raw := []byte("a candidate manager")
	u.client = &http.Client{Transport: updateTransport(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(raw))}, nil
	})}
	path := filepath.Join(u.dir, "manager.next")
	a := releaseAsset{URL: "https://github.com/" + updateRepository + "/releases/download/v0.4.1/alice-homebrew-linux-arm64", Digest: "sha256:" + strings.Repeat("a", 64), Size: int64(len(raw))}
	if e := u.downloadBinary(context.Background(), a, path); e == nil {
		t.Fatal("corrupt file accepted")
	}
	for _, name := range []string{path, path + ".download"} {
		if _, e := os.Stat(name); !os.IsNotExist(e) {
			t.Fatal("unverified staged file retained", name)
		}
	}
	a.Digest = "sha256:" + Digest(raw)
	if e := u.downloadBinary(context.Background(), a, path); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(path)
	if !bytes.Equal(raw, b) {
		t.Fatal("verified download changed")
	}
}
func TestUpdaterSkipsFailedVersionAutomatically(t *testing.T) {
	u, r := updaterFixture(t)
	r.Tag = "v99.0.0"
	u.status.FailedVersion = "99.0.0"
	u.status.Supported = true
	u.saveLocked()
	s, e := u.Check(context.Background(), false)
	if e != nil || s.State != "rolled-back" {
		t.Fatal(s, e)
	}
}
