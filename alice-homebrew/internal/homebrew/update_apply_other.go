//go:build !linux

package homebrew

import "errors"

func launchUpdateHelper(string, string) error {
	return errors.New("automatic replacement is supported by the managed Linux service")
}
func RunUpdateHelper(string) error      { return errors.New("update helper requires Linux") }
func RecoverManagerUpdate(string) error { return errors.New("update recovery requires Linux") }
func processIdentity(int) string        { return "" }
