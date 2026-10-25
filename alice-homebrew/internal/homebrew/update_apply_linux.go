//go:build linux

package homebrew

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func processIdentity(pid int) string {
	b, e := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if e != nil {
		return ""
	}
	end := strings.LastIndexByte(string(b), ')')
	if end < 0 {
		return ""
	}
	fields := strings.Fields(string(b[end+1:]))
	if len(fields) < 20 || fields[0] == "Z" {
		return ""
	}
	return fields[19]
}
func fileDigest(path string) (string, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return "", e
	}
	return Digest(b), nil
}
func regularFile(path string) bool { s, e := os.Lstat(path); return e == nil && s.Mode().IsRegular() }
func syncUpdateDir(path string) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	return f.Sync()
}
func readUpdatePlan(path string) (UpdatePlan, error) {
	var p UpdatePlan
	b, e := os.ReadFile(path)
	if e != nil {
		return p, e
	}
	if len(b) > 65536 {
		return p, errors.New("update journal too large")
	}
	if e = decode(b, &p); e != nil {
		return p, e
	}
	if !filepath.IsAbs(p.Target) || !filepath.IsAbs(p.Data) || filepath.Clean(path) != filepath.Join(p.Data, "manager-update.json") || p.PID < 2 || p.ProcessStart == "" || len(p.Args) == 0 || p.Args[0] != "station" || !validHash(p.SHA256) || !validHash(p.PreviousSHA256) {
		return p, errors.New("invalid update transaction")
	}
	if _, e = parseStableVersion(p.Version); e != nil {
		return p, e
	}
	if !newerVersion(p.Version, p.PreviousVersion) {
		return p, errors.New("update transaction is not an upgrade")
	}
	health, e := url.Parse(p.HealthURL)
	if e != nil || health.Scheme != "http" || health.Hostname() != "127.0.0.1" || health.Port() == "" || health.Path != "/api/about" || health.User != nil || health.RawQuery != "" || health.Fragment != "" {
		return p, errors.New("invalid local health address")
	}
	switch p.Phase {
	case "prepared", "stopped", "swapping", "testing":
	default:
		return p, errors.New("invalid update phase")
	}
	return p, nil
}
func launchUpdateHelper(executable, planPath string) error {
	log, e := os.OpenFile(filepath.Join(filepath.Dir(executable), "update-helper.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0600)
	if e != nil {
		return e
	}
	defer log.Close()
	cmd := exec.Command(executable, "update-helper", planPath)
	cmd.Stdin = nil
	cmd.Stdout = log
	cmd.Stderr = log
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if e = cmd.Start(); e != nil {
		return e
	}
	return cmd.Process.Release()
}
func savePlan(path string, p UpdatePlan, phase string) error {
	p.Phase = phase
	if e := atomicJSON(path, p); e != nil {
		return e
	}
	return syncUpdateDir(filepath.Dir(path))
}
func updateResult(p UpdatePlan, state, message string) error {
	path := filepath.Join(p.Data, "updates.json")
	var status UpdateStatus
	b, _ := os.ReadFile(path)
	_ = json.Unmarshal(b, &status)
	status.Current = p.Version
	status.Latest = p.Version
	status.Supported = true
	status.State = state
	status.Error = message
	status.IntervalHours = 5
	status.NextCheck = time.Now().UTC().Add(UpdateInterval)
	if state == "updated" {
		status.LastUpdate = time.Now().UTC()
		status.FailedVersion = ""
	} else {
		status.Current = p.PreviousVersion
		status.FailedVersion = p.Version
	}
	return atomicJSON(path, status)
}
func startUpdatedManager(p UpdatePlan) (*exec.Cmd, error) {
	log, e := os.OpenFile(filepath.Join(filepath.Dir(p.Target), "service.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0600)
	if e != nil {
		return nil, e
	}
	defer log.Close()
	cmd := exec.Command(p.Target, p.Args...)
	cmd.Stdin = nil
	cmd.Stdout = log
	cmd.Stderr = log
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if e = cmd.Start(); e != nil {
		return nil, e
	}
	// The PID file is used by the existing init hook. It must follow the new
	// service, not this temporary helper. No unrelated process names are killed.
	if e = atomicFile(filepath.Join(filepath.Dir(p.Target), "service.pid"), []byte(strconv.Itoa(cmd.Process.Pid)+"\n")); e != nil {
		cmd.Process.Kill()
		cmd.Wait()
		return nil, e
	}
	return cmd, nil
}
func waitManagerHealth(p UpdatePlan, cmd *exec.Cmd, limit time.Duration) error {
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	client := &http.Client{Timeout: time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("health redirect rejected") }}
	deadline := time.Now().Add(limit)
	for time.Now().Before(deadline) {
		select {
		case e := <-exited:
			return fmt.Errorf("new manager exited before readiness: %v", e)
		default:
		}
		response, e := client.Get(p.HealthURL)
		if e == nil {
			var about struct {
				Mode    string `json:"mode"`
				Version string `json:"version"`
				Ready   bool   `json:"ready"`
			}
			e = json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&about)
			response.Body.Close()
			if e == nil && response.StatusCode == 200 && about.Mode == "station" && about.Version == p.Version && about.Ready {
				return nil
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	_ = cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-exited:
	case <-time.After(5 * time.Second):
		cmd.Process.Kill()
		<-exited
	}
	return errors.New("new manager did not pass its readiness check")
}
func restoreManager(p UpdatePlan) error {
	current, _ := fileDigest(p.Target)
	if current != p.PreviousSHA256 {
		previous, e := fileDigest(p.Target + ".previous")
		if e != nil || previous != p.PreviousSHA256 {
			return errors.New("previous manager checksum does not match rollback journal")
		}
		if e = os.Rename(p.Target+".previous", p.Target); e != nil {
			return e
		}
	}
	backup := filepath.Join(p.Data, "state-before-manager-update.json")
	if p.StateSHA256 != "" {
		b, e := os.ReadFile(backup)
		if e != nil || Digest(b) != p.StateSHA256 {
			return errors.New("state rollback backup checksum mismatch")
		}
		if e = atomicFile(filepath.Join(p.Data, "state.json"), b); e != nil {
			return e
		}
	}
	return syncUpdateDir(filepath.Dir(p.Target))
}
func RunUpdateHelper(path string) error {
	p, e := readUpdatePlan(path)
	if e != nil {
		return e
	}
	if p.Phase != "prepared" {
		return errors.New("update helper requires a prepared transaction")
	}
	self, e := os.Executable()
	resolved, resolveErr := filepath.EvalSymlinks(p.Target)
	if e != nil || resolveErr != nil || filepath.Clean(self) != resolved {
		return errors.New("update helper must be the current manager executable")
	}
	current, e := fileDigest(p.Target)
	if e != nil || current != p.PreviousSHA256 {
		return errors.New("current manager changed before update")
	}
	next, e := fileDigest(p.Target + ".next")
	if e != nil || next != p.SHA256 || !regularFile(p.Target+".next") {
		return errors.New("staged manager changed before installation")
	}
	if processIdentity(p.PID) != p.ProcessStart {
		return errors.New("original manager process is no longer running")
	}
	mapped, e := os.Stat(fmt.Sprintf("/proc/%d/exe", p.PID))
	target, e2 := os.Stat(p.Target)
	if e != nil || e2 != nil || !os.SameFile(mapped, target) {
		return errors.New("refusing to signal a process that is not the managed executable")
	}
	time.Sleep(time.Second) // Allow the initiating HTTP response to finish.
	if e = syscall.Kill(p.PID, syscall.SIGTERM); e != nil {
		return e
	}
	deadline := time.Now().Add(20 * time.Second)
	for processIdentity(p.PID) == p.ProcessStart {
		if time.Now().After(deadline) {
			return errors.New("manager did not stop; binary left unchanged")
		}
		time.Sleep(100 * time.Millisecond)
	}
	// Any failure after stopping the service restores its executable and config.
	installed := false
	newPID := 0
	newStart := ""
	defer func() {
		if !installed {
			if newPID > 0 && newStart != "" && processIdentity(newPID) == newStart {
				syscall.Kill(newPID, syscall.SIGTERM)
				deadline := time.Now().Add(5 * time.Second)
				for processIdentity(newPID) == newStart && time.Now().Before(deadline) {
					time.Sleep(100 * time.Millisecond)
				}
				if processIdentity(newPID) == newStart {
					syscall.Kill(newPID, syscall.SIGKILL)
				}
			}
			if recovery := restoreManager(p); recovery == nil {
				_ = updateResult(p, "rolled-back", "update failed; previous manager restored")
				_ = os.Remove(p.Target + ".next")
				_ = os.Remove(path)
				_, _ = startUpdatedManager(p)
			}
		}
	}()
	if b, e := os.ReadFile(filepath.Join(p.Data, "state.json")); e == nil {
		if e = atomicFile(filepath.Join(p.Data, "state-before-manager-update.json"), b); e != nil {
			return e
		}
		p.StateSHA256 = Digest(b)
	} else if !os.IsNotExist(e) {
		return e
	}
	if e = savePlan(path, p, "stopped"); e != nil {
		return e
	}
	if e = savePlan(path, p, "swapping"); e != nil {
		return e
	}
	if e = os.Rename(p.Target, p.Target+".previous"); e != nil {
		return e
	}
	if e = os.Rename(p.Target+".next", p.Target); e != nil {
		return e
	}
	if e = syncUpdateDir(filepath.Dir(p.Target)); e != nil {
		return e
	}
	if e = savePlan(path, p, "testing"); e != nil {
		return e
	}
	cmd, e := startUpdatedManager(p)
	if e != nil {
		return e
	}
	newPID = cmd.Process.Pid
	newStart = processIdentity(newPID)
	if e = waitManagerHealth(p, cmd, 30*time.Second); e != nil {
		return e
	}
	if e = updateResult(p, "updated", ""); e != nil {
		return e
	}
	if e = os.Remove(path); e != nil {
		return e
	}
	_ = syncUpdateDir(p.Data)
	installed = true
	return nil
}
func RecoverManagerUpdate(path string) error {
	p, e := readUpdatePlan(path)
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	if processIdentity(p.PID) == p.ProcessStart {
		return errors.New("cannot recover an update while the original service is still running")
	}
	if p.Phase == "swapping" || p.Phase == "testing" {
		if e = restoreManager(p); e != nil {
			return e
		}
	}
	if e = updateResult(p, "rolled-back", "interrupted update recovered; previous version retained"); e != nil {
		return e
	}
	_ = os.Remove(p.Target + ".next")
	if e = os.Remove(path); e != nil {
		return e
	}
	return syncUpdateDir(p.Data)
}
