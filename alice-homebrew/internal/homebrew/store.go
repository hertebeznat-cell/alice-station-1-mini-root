package homebrew

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type Submission struct {
	ID          string    `json:"id"`
	Manifest    Manifest  `json:"manifest"`
	SHA256      string    `json:"sha256"`
	Bytes       int       `json:"bytes"`
	Status      string    `json:"status"`
	Scan        string    `json:"scan"`
	ScanDetails string    `json:"scan_details"`
	Created     time.Time `json:"created_at"`
	Reviewed    time.Time `json:"reviewed_at"`
	Notes       string    `json:"notes"`
}
type StoreState struct {
	Revision    uint64                 `json:"revision"`
	Submissions map[string]*Submission `json:"submissions"`
}
type Store struct {
	mu      sync.Mutex
	dir     string
	state   StoreState
	key     ed25519.PrivateKey
	scanner string
	slots   chan struct{}
}

func NewStore(dir, scanner string) (*Store, error) {
	if e := os.MkdirAll(filepath.Join(dir, "packages"), 0700); e != nil {
		return nil, e
	}
	s := &Store{dir: dir, scanner: scanner, slots: make(chan struct{}, 2), state: StoreState{Submissions: map[string]*Submission{}}}
	path := filepath.Join(dir, "catalog-signing.key")
	key, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		_, key, e = ed25519.GenerateKey(rand.Reader)
		if e == nil {
			e = atomicFile(path, key)
		}
	}
	if e != nil {
		return nil, e
	}
	if len(key) != ed25519.PrivateKeySize {
		return nil, errors.New("invalid catalog signing key")
	}
	s.key = ed25519.PrivateKey(key)
	raw, e := os.ReadFile(filepath.Join(dir, "store.json"))
	if e == nil {
		if e = decode(raw, &s.state); e != nil {
			return nil, e
		}
	} else if !os.IsNotExist(e) {
		return nil, e
	}
	if s.state.Submissions == nil {
		s.state.Submissions = map[string]*Submission{}
	}
	return s, nil
}
func (s *Store) PublicKey() string {
	return base64.StdEncoding.EncodeToString(s.key.Public().(ed25519.PublicKey))
}
func (s *Store) save() error { return atomicJSON(filepath.Join(s.dir, "store.json"), s.state) }
func (s *Store) Submit(ctx context.Context, raw []byte) (*Submission, error) {
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	default:
		return nil, errors.New("submission scanner is busy; retry later")
	}
	p, e := Parse(raw)
	if e != nil {
		return nil, e
	}
	if e = ValidateModuleFormat(p); e != nil {
		return nil, e
	}
	s.mu.Lock()
	pending := 0
	for _, v := range s.state.Submissions {
		if v.SHA256 == p.SHA256 {
			s.mu.Unlock()
			return nil, errors.New("this exact package was already submitted")
		}
		if v.Status == "pending" {
			pending++
		}
	}
	if pending >= 32 {
		s.mu.Unlock()
		return nil, errors.New("review queue is full")
	}
	if e = putPackage(filepath.Join(s.dir, "packages"), p); e != nil {
		s.mu.Unlock()
		return nil, e
	}
	s.mu.Unlock()
	v := &Submission{ID: randomID(), Manifest: p.Manifest, SHA256: p.SHA256, Bytes: len(raw), Status: "pending", Scan: "not_configured", Created: time.Now().UTC()}
	if s.scanner != "" {
		scanCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
		defer cancel()
		cmd := exec.CommandContext(scanCtx, s.scanner, "--no-summary", "--", filepath.Join(s.dir, "packages", p.SHA256+".aliceplugin"))
		output, err := cmd.CombinedOutput()
		v.ScanDetails = string(output)
		if len(v.ScanDetails) > 2000 {
			v.ScanDetails = v.ScanDetails[:2000]
		}
		v.Scan = "clean"
		if err != nil {
			v.Scan = "scanner_error"
			if x, ok := err.(*exec.ExitError); ok && x.ExitCode() == 1 {
				v.Scan = "infected"
			}
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state.Submissions[v.ID] = v
	if e = s.save(); e != nil {
		delete(s.state.Submissions, v.ID)
		return nil, e
	}
	return clone(v), nil
}
func (s *Store) Review(id, action, notes string, ack bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	v := s.state.Submissions[id]
	if v == nil {
		return errors.New("submission not found")
	}
	if len(notes) > 2000 {
		return errors.New("review notes too long")
	}
	old := clone(*v)
	revision := s.state.Revision
	switch action {
	case "approve":
		if !ack {
			return errors.New("confirm manual code and behavior review")
		}
		if v.Status != "pending" {
			return errors.New("only pending versions can be approved")
		}
		if v.Scan == "infected" || v.Scan == "scanner_error" {
			return errors.New("resolve the malware scanner result before approval")
		}
		for _, other := range s.state.Submissions {
			if other.ID != id && other.Manifest.ID == v.Manifest.ID && other.Manifest.Version == v.Manifest.Version && (other.Status == "approved" || other.Status == "revoked") {
				return errors.New("published versions are immutable; submit a new version")
			}
		}
		v.Status = "approved"
		s.state.Revision++
	case "reject":
		if v.Status != "pending" {
			return errors.New("only pending versions can be rejected")
		}
		v.Status = "rejected"
	case "revoke":
		if v.Status != "approved" {
			return errors.New("only approved versions can be revoked")
		}
		v.Status = "revoked"
		s.state.Revision++
	default:
		return errors.New("invalid review action")
	}
	v.Notes = notes
	v.Reviewed = time.Now().UTC()
	if e := s.save(); e != nil {
		*v = old
		s.state.Revision = revision
		return e
	}
	return nil
}
func (s *Store) Queue() []*Submission {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []*Submission{}
	for _, v := range s.state.Submissions {
		out = append(out, clone(v))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Created.After(out[j].Created) })
	return out
}
func (s *Store) Catalog() (Envelope, error) {
	return s.CatalogFor(2)
}
func (s *Store) CatalogFor(maxAPI int) (Envelope, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().UTC()
	c := Catalog{Format: 1, Revision: s.state.Revision, Generated: now, Expires: now.Add(365 * 24 * time.Hour), Entries: []Entry{}, Revoked: []string{}}
	for _, v := range s.state.Submissions {
		if v.Status == "approved" && v.Manifest.API <= maxAPI {
			c.Entries = append(c.Entries, Entry{Manifest: v.Manifest, SHA256: v.SHA256, Bytes: v.Bytes, Download: "../packages/" + v.SHA256 + ".aliceplugin", ReviewedAt: v.Reviewed})
		}
		if v.Status == "revoked" {
			c.Revoked = append(c.Revoked, v.SHA256)
		}
	}
	c.Entries = latestCatalogEntries(c.Entries)
	sort.Slice(c.Entries, func(i, j int) bool {
		a, b := c.Entries[i], c.Entries[j]
		return a.Manifest.ID+"@"+a.Manifest.Version < b.Manifest.ID+"@"+b.Manifest.Version
	})
	sort.Strings(c.Revoked)
	return SignCatalog(c, s.key)
}
func (s *Store) Download(hash string, reviewer bool) ([]byte, error) {
	if !validHash(hash) {
		return nil, errors.New("invalid package hash")
	}
	s.mu.Lock()
	found := false
	for _, v := range s.state.Submissions {
		if v.SHA256 == hash && (reviewer || v.Status == "approved") {
			found = true
			break
		}
	}
	s.mu.Unlock()
	if !found {
		return nil, errors.New("package is not published")
	}
	raw, e := os.ReadFile(filepath.Join(s.dir, "packages", hash+".aliceplugin"))
	if e == nil && Digest(raw) != hash {
		return nil, errors.New("stored package is corrupt")
	}
	return raw, e
}
