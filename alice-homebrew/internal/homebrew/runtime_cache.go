package homebrew

import (
	"errors"
	"os"
	"path/filepath"
)

// Only versioned executables in the manager-owned runtimes directory are
// disposable. Configs and files created by plugins live outside that directory.
// A rollback recreates its executable from the retained, verified package.
func cleanRuntimeCache(dataRoot, id, currentHash string) error {
	if !idRE.MatchString(id) || !validHash(currentHash) {
		return errors.New("invalid runtime cache identity")
	}
	root, err := filepath.Abs(dataRoot)
	if err != nil {
		return err
	}
	plugin := filepath.Join(root, id)
	runtimes := filepath.Join(plugin, "runtimes")
	// Never follow a plugin-controlled link to a different data directory.
	for _, path := range []string{root, plugin, runtimes} {
		info, err := os.Lstat(path)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("runtime cache path is not a regular directory")
		}
	}
	entries, err := os.ReadDir(runtimes)
	if err != nil {
		return err
	}
	var cleanupErr error
	for _, entry := range entries {
		name := entry.Name()
		if name == currentHash || !validHash(name) || !entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		target := filepath.Join(runtimes, name)
		if relative, err := filepath.Rel(runtimes, target); err != nil || relative != name {
			return errors.New("invalid runtime cache path")
		}
		cleanupErr = errors.Join(cleanupErr, os.RemoveAll(target))
	}
	return cleanupErr
}
