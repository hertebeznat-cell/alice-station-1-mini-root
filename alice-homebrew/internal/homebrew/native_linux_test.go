//go:build linux

package homebrew

import (
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestNativeSupervisor(t *testing.T) {
	cmd := exec.Command("/bin/sh", "-c", "printf 'native test log\\n'; sleep 30")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	r, e := startNativeProcess(cmd)
	if e != nil {
		t.Fatal(e)
	}
	defer r.Close()
	deadline := time.Now().Add(time.Second)
	for len(r.LogLines()) == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(strings.Join(r.LogLines(), " "), "native test log") {
		t.Fatal(r.LogLines())
	}
	r.Close()
	if _, e = r.Tick(nil, 0); e == nil {
		t.Fatal("exited service reported healthy")
	}
}
