package homebrew

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

func randomID() string {
	b := make([]byte, 24)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b)
}
func atomicJSON(path string, value any) error {
	b, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	return atomicFile(path, append(b, '\n'))
}
func atomicFile(path string, b []byte) error {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".pending-")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e != nil {
		return e
	}
	if ce != nil {
		return ce
	}
	return os.Rename(name, path)
}
func secret(path string) (string, error) {
	b, e := os.ReadFile(path)
	if e == nil {
		return string(b), nil
	}
	if !os.IsNotExist(e) {
		return "", e
	}
	s := randomID()
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return "", e
	}
	_, e = f.WriteString(s)
	f.Close()
	return s, e
}
func AccessToken(path string) (string, error) { return secret(path) }
func putPackage(dir string, p *Package) error {
	if !validHash(p.SHA256) {
		return errors.New("invalid package hash")
	}
	path := filepath.Join(dir, p.SHA256+".aliceplugin")
	if b, e := os.ReadFile(path); e == nil {
		if Digest(b) != p.SHA256 {
			return errors.New("existing package is corrupt")
		}
		return nil
	}
	return atomicFile(path, p.Raw)
}
func clone[T any](v T) T { b, _ := json.Marshal(v); var out T; json.Unmarshal(b, &out); return out }
