// Package hardware exposes Linux interfaces used by native Mini 1 plugins.
// Opening a device does not start microphone capture or enable a sensor.
package hardware

import (
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type InputDevice struct {
	Name string `json:"name"`
	Path string `json:"path"`
}
type InputEvent struct {
	Seconds      int64
	Microseconds int64
	Type         uint16
	Code         uint16
	Value        int32
}

// Inputs discovers names on this boot; event numbers are not stable IDs.
func Inputs() []InputDevice {
	out := []InputDevice{}
	paths, _ := filepath.Glob("/sys/class/input/event*")
	for _, p := range paths {
		b, e := os.ReadFile(filepath.Join(p, "device/name"))
		if e == nil {
			out = append(out, InputDevice{strings.TrimSpace(string(b)), "/dev/input/" + filepath.Base(p)})
		}
	}
	return out
}
func FindInput(name string) (string, error) {
	for _, d := range Inputs() {
		if d.Name == name {
			return d.Path, nil
		}
	}
	return "", fmt.Errorf("input device not found: %s", name)
}

// ReadInput decodes Linux ARM64 input_event, including signed ABS distances and
// key press (1), release (0), repeat (2). It performs no EVIOCGRAB.
func ReadInput(r io.Reader) (InputEvent, error) {
	var b [24]byte
	if _, e := io.ReadFull(r, b[:]); e != nil {
		return InputEvent{}, e
	}
	return InputEvent{int64(binary.LittleEndian.Uint64(b[0:8])), int64(binary.LittleEndian.Uint64(b[8:16])), binary.LittleEndian.Uint16(b[16:18]), binary.LittleEndian.Uint16(b[18:20]), int32(binary.LittleEndian.Uint32(b[20:24]))}, nil
}

// ReadInventory returns the manager's read-only hardware snapshot.
func ReadInventory() ([]byte, error) {
	path := os.Getenv("ALICE_HARDWARE_PATH")
	if path == "" {
		return nil, fmt.Errorf("ALICE_HARDWARE_PATH is not set")
	}
	return os.ReadFile(path)
}
