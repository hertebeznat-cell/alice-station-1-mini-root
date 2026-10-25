package homebrew

import (
	"fmt"
	"sync"
)

type Ring struct {
	mu        sync.Mutex
	frame     []byte
	pending   []byte
	lastError string
	write     func([]byte) error
	close     func() error
	Hardware  bool
}

func NewRing(hardware bool) (*Ring, error) {
	r := &Ring{frame: make([]byte, 36), Hardware: hardware}
	if hardware {
		w, c, e := openNativeRing()
		if e != nil {
			return nil, e
		}
		r.write = w
		r.close = c
	}
	return r, nil
}
func (r *Ring) Publish(frame []byte) {
	if len(frame) != 36 {
		return
	}
	r.mu.Lock()
	r.frame = append(r.frame[:0], frame...)
	r.pending = append(r.pending[:0], frame...)
	r.mu.Unlock()
}
func (r *Ring) Flush() {
	r.mu.Lock()
	frame := r.pending
	r.pending = nil
	r.mu.Unlock()
	if len(frame) == 36 && r.write != nil {
		if e := r.write(frame); e != nil {
			r.mu.Lock()
			r.lastError = e.Error()
			r.mu.Unlock()
		}
	}
}
func (r *Ring) Snapshot() map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	colors := []string{}
	for i := 0; i < 12; i++ {
		colors = append(colors, fmt.Sprintf("#%02x%02x%02x", r.frame[i*3+2], r.frame[i*3+1], r.frame[i*3]))
	}
	return map[string]any{"colors": colors, "hardware": r.Hardware, "error": r.lastError}
}
func (r *Ring) Close() {
	if r.close != nil {
		r.close()
	}
}
