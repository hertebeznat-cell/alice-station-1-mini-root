package homebrew

import (
 "crypto/tls"
 "crypto/x509"
 _ "embed"
 "errors"
 "net/http"
 "sync"
)

// Mozilla CA store, converted by curl; MPL-2.0. See certs/SOURCE.json.
//go:embed certs/cacert.pem
var bundledCAPEM []byte

var tlsRootsOnce sync.Once
var tlsRoots *x509.CertPool

func rootsWithBundle(system *x509.CertPool, bundle []byte) (*x509.CertPool, error) {
 var roots *x509.CertPool
 if system == nil { roots=x509.NewCertPool() } else { roots=system.Clone() }
 if !roots.AppendCertsFromPEM(bundle) { return nil,errors.New("bundled CA certificates are invalid") }
 return roots,nil
}
func trustedTLSRoots() *x509.CertPool {
 tlsRootsOnce.Do(func(){
  system,_:=x509.SystemCertPool()
  roots,err:=rootsWithBundle(system,bundledCAPEM)
  if err!=nil { panic(err) }
  tlsRoots=roots
 })
 return tlsRoots
}
func transportWithRoots(roots *x509.CertPool) *http.Transport {
 var transport *http.Transport
 if standard,ok:=http.DefaultTransport.(*http.Transport);ok {transport=standard.Clone()} else {transport=&http.Transport{Proxy:http.ProxyFromEnvironment}}
 transport.TLSClientConfig=&tls.Config{RootCAs:roots,MinVersion:tls.VersionTLS12}
 return transport
}
var transportOnce sync.Once
var sharedTrustedTransport *http.Transport
func trustedHTTPTransport() *http.Transport {
 transportOnce.Do(func(){sharedTrustedTransport=transportWithRoots(trustedTLSRoots())})
 return sharedTrustedTransport
}
