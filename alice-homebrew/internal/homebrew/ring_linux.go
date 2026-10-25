//go:build linux

package homebrew

import (
	"errors"
	"syscall"
)

func openNativeRing() (func([]byte) error, func() error, error) {
	fd, e := syscall.Open("/dev/i2c-0", syscall.O_RDWR|syscall.O_CLOEXEC, 0)
	if e != nil {
		return nil, nil, e
	}
	if _, _, err := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), 0x0703, 0x3c); err != 0 {
		syscall.Close(fd)
		return nil, nil, err
	}
	writeReg := func(reg, value byte) error {
		n, err := syscall.Write(fd, []byte{reg, value})
		if err == nil && n != 2 {
			return errors.New("short I2C write")
		}
		return err
	}
	initialized := false
	write := func(frame []byte) error {
		if len(frame) != 36 {
			return errors.New("invalid ring frame")
		}
		if !initialized {
			if e := writeReg(0, 1); e != nil {
				return e
			}
			for ch := byte(1); ch <= 36; ch++ {
				if e := writeReg(0x25+ch, 3); e != nil {
					return e
				}
			}
			initialized = true
		}
		for ch, value := range frame {
			if e := writeReg(byte(ch+1), value); e != nil {
				return e
			}
		}
		if e := writeReg(0x4a, 0); e != nil {
			return e
		}
		return writeReg(0x25, 0)
	}
	return write, func() error { return syscall.Close(fd) }, nil
}
func availableBytes(dir string) uint64 {
	var s syscall.Statfs_t
	if syscall.Statfs(dir, &s) != nil {
		return 0
	}
	return s.Bavail * uint64(s.Bsize)
}
