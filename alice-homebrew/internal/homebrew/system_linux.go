//go:build linux

package homebrew

import (
	"os"
	"strconv"
	"strings"
	"syscall"
)

func diskUsage(dir string) DiskUsage {
	var s syscall.Statfs_t
	if syscall.Statfs(dir, &s) != nil {
		return DiskUsage{}
	}
	return DiskUsage{Available: true, TotalBytes: s.Blocks * uint64(s.Bsize), UsedBytes: (s.Blocks - s.Bfree) * uint64(s.Bsize), AvailableBytes: s.Bavail * uint64(s.Bsize)}
}
func memoryUsage() MemoryUsage {
	b, e := os.ReadFile("/proc/meminfo")
	if e != nil {
		return MemoryUsage{}
	}
	values := map[string]uint64{}
	for _, line := range strings.Split(string(b), "\n") {
		p := strings.Fields(line)
		if len(p) >= 2 {
			n, e := strconv.ParseUint(p[1], 10, 64)
			if e == nil {
				values[strings.TrimSuffix(p[0], ":")] = n * 1024
			}
		}
	}
	m := MemoryUsage{Available: values["MemTotal"] > 0, TotalBytes: values["MemTotal"], AvailableBytes: values["MemAvailable"]}
	if m.AvailableBytes == 0 {
		m.AvailableBytes = values["MemFree"]
	}
	if m.TotalBytes > m.AvailableBytes {
		m.UsedBytes = m.TotalBytes - m.AvailableBytes
	}
	if b, e = os.ReadFile("/proc/self/statm"); e == nil {
		fields := strings.Fields(string(b))
		if len(fields) > 1 {
			resident, e := strconv.ParseUint(fields[1], 10, 64)
			if e == nil {
				m.ProcessBytes = resident * uint64(os.Getpagesize())
			}
		}
	}
	return m
}
