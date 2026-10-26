package homebrew

import (
 "crypto/tls"
 "crypto/x509"
 "encoding/pem"
 "errors"
 "net/http"
 "net/http/httptest"
 "testing"
 "time"
)

func TestBundledRootsWorkWithoutSystemStore(t *testing.T) {
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(http.StatusNoContent)}));defer server.Close()
 certificate:=pem.EncodeToMemory(&pem.Block{Type:"CERTIFICATE",Bytes:server.Certificate().Raw})
 roots,err:=rootsWithBundle(nil,certificate);if err!=nil{t.Fatal(err)}
 transport:=transportWithRoots(roots);defer transport.CloseIdleConnections()
 client:=&http.Client{Transport:transport,Timeout:3*time.Second}
 response,err:=client.Get(server.URL);if err!=nil{t.Fatal(err)};response.Body.Close()
 if response.StatusCode!=http.StatusNoContent{t.Fatal(response.StatusCode)}
}
func TestBundledRootsRejectUnknownAuthority(t *testing.T) {
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(http.StatusNoContent)}));defer server.Close()
 roots,err:=rootsWithBundle(nil,bundledCAPEM);if err!=nil{t.Fatal(err)}
 transport:=transportWithRoots(roots);defer transport.CloseIdleConnections()
 client:=&http.Client{Transport:transport,Timeout:3*time.Second}
 response,err:=client.Get(server.URL)
 if err==nil{response.Body.Close();t.Fatal("untrusted certificate accepted")}
 var verification *tls.CertificateVerificationError
 if !errors.As(err,&verification){t.Fatal("expected certificate rejection, got",err)}
 if transport.TLSClientConfig.InsecureSkipVerify||transport.TLSClientConfig.MinVersion<tls.VersionTLS12{t.Fatal("TLS verification weakened")}
}
func TestBundledRootsStillVerifyHostnameAndDates(t *testing.T) {
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(http.StatusNoContent)}));defer server.Close()
 roots,err:=rootsWithBundle(nil,pem.EncodeToMemory(&pem.Block{Type:"CERTIFICATE",Bytes:server.Certificate().Raw}));if err!=nil{t.Fatal(err)}
 for _,scenario:=range []string{"hostname","expired"}{t.Run(scenario,func(t *testing.T){
  transport:=transportWithRoots(roots);defer transport.CloseIdleConnections()
  if scenario=="hostname"{transport.TLSClientConfig.ServerName="not-this-server.invalid"}else{transport.TLSClientConfig.Time=func()time.Time{return server.Certificate().NotAfter.Add(time.Hour)}}
  response,err:=(&http.Client{Transport:transport,Timeout:3*time.Second}).Get(server.URL)
  if err==nil{response.Body.Close();t.Fatal("invalid certificate accepted")}
  var verification *tls.CertificateVerificationError
  if !errors.As(err,&verification){t.Fatal("expected certificate rejection, got",err)}
 })}
}
func TestBundledRootsPreserveExistingTrustWithoutMutation(t *testing.T) {
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){}));defer server.Close()
 system:=x509.NewCertPool();system.AddCert(server.Certificate());before:=len(system.Subjects())
 roots,err:=rootsWithBundle(system,bundledCAPEM);if err!=nil{t.Fatal(err)}
 if len(system.Subjects())!=before||len(roots.Subjects())<80{t.Fatal("unexpected root pool contents")}
 if _,err:=server.Certificate().Verify(x509.VerifyOptions{Roots:roots,DNSName:"127.0.0.1"});err!=nil{t.Fatal("existing root was lost:",err)}
}
