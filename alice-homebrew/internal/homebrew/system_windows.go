//go:build windows

package homebrew

import (
	"golang.org/x/sys/windows"
	"unsafe"
)

func diskUsage(dir string) DiskUsage {
	p, e := windows.UTF16PtrFromString(dir)
	if e != nil {
		return DiskUsage{}
	}
	var available, total, free uint64
	if windows.GetDiskFreeSpaceEx(p, &available, &total, &free) != nil {
		return DiskUsage{}
	}
	return DiskUsage{Available: true, TotalBytes: total, UsedBytes: total - free, AvailableBytes: available}
}
func memoryUsage() MemoryUsage {
	var status struct {
		Length, Load                                                                                         uint32
		TotalPhys, AvailPhys, TotalPageFile, AvailPageFile, TotalVirtual, AvailVirtual, AvailExtendedVirtual uint64
	}
	status.Length = uint32(unsafe.Sizeof(status))
	ok, _, _ := windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalMemoryStatusEx").Call(uintptr(unsafe.Pointer(&status)))
	if ok == 0 {
		return MemoryUsage{}
	}
	return MemoryUsage{Available: true, TotalBytes: status.TotalPhys, AvailableBytes: status.AvailPhys, UsedBytes: status.TotalPhys - status.AvailPhys}
}
