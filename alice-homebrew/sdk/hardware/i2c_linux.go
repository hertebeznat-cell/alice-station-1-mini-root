//go:build linux

package hardware

import (
	"fmt"
	"os"
	"syscall"
)

type I2C struct{ file *os.File }

func OpenI2C(path string, address uint16) (*I2C, error) {
	if address > 0x7f {
		return nil, fmt.Errorf("only 7-bit I2C addresses are supported")
	}
	f, e := os.OpenFile(path, os.O_RDWR, 0)
	if e != nil {
		return nil, e
	}
	if _, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), 0x0703, uintptr(address)); e != 0 {
		f.Close()
		return nil, e
	}
	return &I2C{f}, nil
}
func (b *I2C) Close() error { return b.file.Close() }
func (b *I2C) Write(data []byte) error {
	n, e := b.file.Write(data)
	if e == nil && n != len(data) {
		return fmt.Errorf("short I2C write")
	}
	return e
}
func (b *I2C) Read(data []byte) (int, error) { return b.file.Read(data) }

// Register operations are deliberately explicit: callers own device arbitration.
func (b *I2C) WriteRegister(register, value byte) error { return b.Write([]byte{register, value}) }
