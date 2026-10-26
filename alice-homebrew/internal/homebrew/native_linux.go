//go:build linux

package homebrew

import (
	"errors"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Native services require full-root consent at installation and again before
// enabling. Inspection and moderation validate ELF data without executing it.
type NativeRunner struct {
	cmd       *exec.Cmd
	done      chan struct{}
	mu        sync.Mutex
	logs      []string
	pending   string
	err       error
	closeOnce sync.Once
	ringAllowed bool
	frame []byte
}

func (r *NativeRunner) Write(b []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pending += strings.ToValidUTF8(string(b), "?")
	for {
		i := strings.IndexByte(r.pending, '\n')
		if i < 0 {
			break
		}
		r.addLine(r.pending[:i])
		r.pending = r.pending[i+1:]
	}
	if len(r.pending) > 2048 {
		r.addLine(r.pending[:2048])
		r.pending = ""
	}
	return len(b), nil
}
func (r *NativeRunner) addLine(s string) {
	if strings.HasPrefix(s, "@alice-ring ") && r.ringAllowed {
		frame, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(s, "@alice-ring "))
		if err == nil && len(frame) == 36 { r.frame = frame; return }
	}
	if len(s) > 2048 {
		s = s[:2048]
	}
	r.logs = append(r.logs, s)
	if len(r.logs) > 32 {
		r.logs = r.logs[len(r.logs)-32:]
	}
}
func NewNativeRunner(p *Package, config map[string]any, root string) (PluginRunner, error) {
	if runtime.GOARCH != "arm64" {
		return nil, errors.New("native plugin requires Linux ARM64 speaker")
	}
	if !Has(p.Manifest, "system.full-access") {
		return nil, errors.New("missing full-access declaration")
	}
	if e := validateELF(p.Module); e != nil {
		return nil, e
	}
	data := filepath.Join(root, "plugin-data", p.Manifest.ID)
	dir := filepath.Join(data, "runtimes", p.SHA256)
	if e := os.MkdirAll(dir, 0700); e != nil {
		return nil, e
	}
	executable := filepath.Join(dir, "plugin.elf")
	existing, e := os.ReadFile(executable)
	if e != nil || Digest(existing) != p.Manifest.ModuleSHA256 {
		if e = atomicFile(executable, p.Module); e != nil {
			return nil, e
		}
	}
	if e = os.Chmod(executable, 0700); e != nil {
		return nil, e
	}
	configPath := filepath.Join(data, "config.json")
	if e = atomicJSON(configPath, config); e != nil {
		return nil, e
	}
	hardwarePath := filepath.Join(data, "hardware.json")
	if e = atomicJSON(hardwarePath, HardwareInventory()); e != nil {
		return nil, e
	}
	executable, _ = filepath.Abs(executable)
	data, _ = filepath.Abs(data)
	configPath, _ = filepath.Abs(configPath)
	hardwarePath, _ = filepath.Abs(hardwarePath)
	cmd := exec.Command(executable)
	cmd.Dir = data
	cmd.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8", "HOME=" + data, "TMPDIR=" + data, "ALICE_PLUGIN_ID=" + p.Manifest.ID, "ALICE_PLUGIN_DATA=" + data, "ALICE_CONFIG_PATH=" + configPath, "ALICE_HARDWARE_PATH=" + hardwarePath}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGTERM}
	r, err := startNativeProcess(cmd, Has(p.Manifest, "led.ring"))
	return r, err
}
func startNativeProcess(cmd *exec.Cmd, ringPermission ...bool) (*NativeRunner, error) {
	r := &NativeRunner{cmd: cmd, done: make(chan struct{}), ringAllowed: len(ringPermission)>0 && ringPermission[0]}
	cmd.Stdout = r
	cmd.Stderr = r
	if e := cmd.Start(); e != nil {
		return nil, e
	}
	go func() {
		e := cmd.Wait()
		r.mu.Lock()
		if e == nil {
			e = errors.New("native service exited")
		}
		r.err = e
		r.mu.Unlock()
		close(r.done)
	}()
	return r, nil
}
func (r *NativeRunner) Tick(_ map[string]any, _ uint32) ([]byte, error) {
	select {
	case <-r.done:
		r.mu.Lock()
		defer r.mu.Unlock()
		return nil, fmt.Errorf("native plugin stopped: %w", r.err)
	default:
		r.mu.Lock()
		frame := r.frame
		r.frame = nil
		r.mu.Unlock()
		return frame, nil
	}
}
func (r *NativeRunner) Close() {
	r.closeOnce.Do(func() {
		syscall.Kill(-r.cmd.Process.Pid, syscall.SIGTERM)
		select {
		case <-r.done:
		case <-time.After(1500 * time.Millisecond):
		}
		syscall.Kill(-r.cmd.Process.Pid, syscall.SIGKILL)
		select {
		case <-r.done:
		case <-time.After(time.Second):
		}
	})
}
func (r *NativeRunner) LogLines() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string{}, r.logs...)
}
func (r *NativeRunner) MemoryBytes() uint32 { return 0 }

// Read-only inventory: no device capture, sensor activation or hardware writes.
func HardwareInventory() map[string]any {
	inputs := []map[string]string{}
	paths, _ := filepath.Glob("/sys/class/input/event*")
	for _, p := range paths {
		b, _ := os.ReadFile(filepath.Join(p, "device/name"))
		inputs = append(inputs, map[string]string{"name": strings.TrimSpace(string(b)), "device": "/dev/input/" + filepath.Base(p)})
	}
	nodes := map[string][]string{}
	for name, pattern := range map[string]string{"audio": "/dev/snd/*", "i2c": "/dev/i2c-*", "gpio": "/dev/gpiochip*", "serial": "/dev/ttyS*", "leds": "/sys/class/leds/*", "thermal": "/sys/class/thermal/thermal_zone*"} {
		nodes[name], _ = filepath.Glob(pattern)
	}
	cards, _ := os.ReadFile("/proc/asound/cards")
	pcm, _ := os.ReadFile("/proc/asound/pcm")
	return map[string]any{"os": "linux", "architecture": runtime.GOARCH, "inputs": inputs, "devices": nodes, "alsa_cards": string(cards), "alsa_pcm": string(pcm), "native_full_access": true}
}
