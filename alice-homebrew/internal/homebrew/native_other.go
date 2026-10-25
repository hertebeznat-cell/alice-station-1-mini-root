//go:build !linux

package homebrew

import "errors"

func NewNativeRunner(_ *Package, _ map[string]any, _ string) (PluginRunner, error) {
	return nil, errors.New("native plugins run on Linux ARM64 speakers")
}
func HardwareInventory() map[string]any { return map[string]any{"native_full_access": false} }
