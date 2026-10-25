//go:build !linux && !windows

package homebrew

func diskUsage(dir string) DiskUsage { return DiskUsage{} }
func memoryUsage() MemoryUsage       { return MemoryUsage{} }
