//go:build !linux

package homebrew

import "errors"

func openNativeRing() (func([]byte) error, func() error, error) {
	return nil, nil, errors.New("hardware LED access requires Linux on the speaker")
}
func availableBytes(dir string) uint64 { return ^uint64(0) }
