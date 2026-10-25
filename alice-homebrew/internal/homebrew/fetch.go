package homebrew

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

func manualURL(raw string, allowHTTP bool) (*url.URL, error) {
	u, e := url.Parse(raw)
	if e != nil || u.Host == "" || u.User != nil || u.Fragment != "" {
		return nil, errors.New("invalid package URL")
	}
	local := false
	if ip := net.ParseIP(u.Hostname()); ip != nil {
		local = ip.IsPrivate() || ip.IsLoopback()
	} else {
		local = u.Hostname() == "localhost" || strings.HasSuffix(strings.ToLower(u.Hostname()), ".local")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && (local || allowHTTP)) {
		return nil, errors.New("use HTTPS, or an HTTP address in your local network")
	}
	return u, nil
}

// FetchURL runs in the station service, not in the visitor's browser. A manual
// URL never inherits the catalog's reviewed status, even if the host is GitHub.
func (m *Manager) FetchURL(ctx context.Context, raw, expected string) (map[string]any, error) {
	if expected != "" && !validHash(expected) {
		return nil, errors.New("expected SHA-256 must contain 64 lowercase hexadecimal characters")
	}
	origin, e := manualURL(raw, m.AllowLocalStore)
	if e != nil {
		return nil, e
	}
	client := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) > 5 {
			return errors.New("too many download redirects")
		}
		if _, e := manualURL(req.URL.String(), m.AllowLocalStore); e != nil {
			return e
		}
		if origin.Scheme == "https" && req.URL.Scheme != "https" {
			return errors.New("HTTPS download cannot redirect to HTTP")
		}
		if origin.Scheme == "http" && req.URL.Host != origin.Host {
			return errors.New("local HTTP download cannot redirect to a different host")
		}
		return nil
	}}
	req, e := http.NewRequestWithContext(ctx, "GET", origin.String(), nil)
	if e != nil {
		return nil, e
	}
	response, e := client.Do(req)
	if e != nil {
		return nil, e
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("package HTTP %d", response.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(response.Body, MaxPackage+1))
	if e != nil {
		return nil, e
	}
	if len(b) > MaxPackage {
		return nil, errors.New("download exceeds the package size limit")
	}
	if expected != "" && Digest(b) != expected {
		return nil, errors.New("downloaded package SHA-256 does not match")
	}
	return m.Stage(b, false)
}
