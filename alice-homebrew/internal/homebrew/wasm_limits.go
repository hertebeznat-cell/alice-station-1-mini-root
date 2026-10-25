package homebrew

import (
	"bytes"
	"errors"
	"fmt"
)

// Bound declarations before handing untrusted modules to the runtime. Linear
// memory limits alone do not constrain function tables or parser allocations.
type wasmReader struct {
	b []byte
	i int
}

func (r *wasmReader) byte() (byte, error) {
	if r.i >= len(r.b) {
		return 0, errors.New("truncated WebAssembly")
	}
	v := r.b[r.i]
	r.i++
	return v, nil
}
func (r *wasmReader) u32() (uint32, error) {
	var n uint32
	for i := 0; i < 5; i++ {
		b, e := r.byte()
		if e != nil {
			return 0, e
		}
		if i == 4 && b > 15 {
			return 0, errors.New("invalid WebAssembly integer")
		}
		n |= uint32(b&127) << uint(i*7)
		if b&128 == 0 {
			return n, nil
		}
	}
	return 0, errors.New("invalid WebAssembly integer")
}
func (r *wasmReader) name() (string, error) {
	n, e := r.u32()
	if e != nil || n > 128 || uint64(r.i)+uint64(n) > uint64(len(r.b)) {
		return "", errors.New("invalid import name")
	}
	s := string(r.b[r.i : r.i+int(n)])
	r.i += int(n)
	return s, nil
}
func wasmLimits(module []byte) error {
	if len(module) < 8 || len(module) > MaxModule || !bytes.Equal(module[:8], []byte{0, 97, 115, 109, 1, 0, 0, 0}) {
		return errors.New("invalid WebAssembly header")
	}
	r := wasmReader{b: module, i: 8}
	sections := 0
	for r.i < len(r.b) {
		sections++
		if sections > 64 {
			return errors.New("too many WebAssembly sections")
		}
		id, e := r.byte()
		if e != nil {
			return e
		}
		size, e := r.u32()
		if e != nil || uint64(r.i)+uint64(size) > uint64(len(r.b)) {
			return errors.New("invalid WebAssembly section size")
		}
		part := wasmReader{b: r.b[r.i : r.i+int(size)]}
		r.i += int(size)
		if id == 0 {
			continue
		}
		count, e := part.u32()
		if e != nil {
			return e
		}
		switch id {
		case 1:
			if count > 1024 {
				return errors.New("too many function types")
			}
			for j := uint32(0); j < count; j++ {
				kind, e := part.byte()
				if e != nil || kind != 0x60 {
					return errors.New("unsupported WebAssembly type")
				}
				for _, limit := range []uint32{32, 4} {
					n, e := part.u32()
					if e != nil || n > limit {
						return errors.New("function signature exceeds limits")
					}
					for k := uint32(0); k < n; k++ {
						v, e := part.byte()
						if e != nil || (v != 0x7f && v != 0x7e && v != 0x7d && v != 0x7c) {
							return errors.New("only numeric WebAssembly values are supported")
						}
					}
				}
			}
		case 2:
			if count > 16 {
				return errors.New("too many imports")
			}
			for j := uint32(0); j < count; j++ {
				namespace, e := part.name()
				if e != nil {
					return e
				}
				name, e := part.name()
				if e != nil {
					return e
				}
				kind, e := part.byte()
				if e != nil {
					return e
				}
				if namespace != "alice" || kind != 0 || (name != "config_number" && name != "config_string" && name != "led_write" && name != "log") {
					return fmt.Errorf("import is not allowed: %s.%s", namespace, name)
				}
				if _, e = part.u32(); e != nil {
					return e
				}
			}
		case 4:
			if count > 1 {
				return errors.New("at most one function table is allowed")
			}
			for j := uint32(0); j < count; j++ {
				kind, e := part.byte()
				if e != nil || kind != 0x70 {
					return errors.New("only funcref tables are allowed")
				}
				flags, e := part.u32()
				if e != nil || flags != 1 {
					return errors.New("function table must declare a bounded maximum")
				}
				min, e := part.u32()
				if e != nil {
					return e
				}
				max, e := part.u32()
				if e != nil || max > 1024 || min > max {
					return errors.New("function table exceeds 1024 entries")
				}
			}
		case 5:
			if count != 1 {
				return errors.New("exactly one WebAssembly memory is required")
			}
			flags, e := part.u32()
			if e != nil || flags > 1 {
				return errors.New("shared and 64-bit memory are not supported")
			}
			min, e := part.u32()
			if e != nil || min > 32 {
				return errors.New("memory exceeds 2 MiB")
			}
			if flags == 1 {
				max, e := part.u32()
				if e != nil || max > 32 || min > max {
					return errors.New("memory exceeds 2 MiB")
				}
			}
		case 3, 10:
			if count > 1024 {
				return errors.New("too many WebAssembly functions")
			}
		case 6:
			if count > 256 {
				return errors.New("too many globals")
			}
		case 7:
			if count > 64 {
				return errors.New("too many exports")
			}
		case 9:
			if count > 64 {
				return errors.New("too many element segments")
			}
		case 11, 12:
			if count > 128 {
				return errors.New("too many data segments")
			}
		case 8: // This section contains a function index, not a vector count.
		default:
			return errors.New("unsupported WebAssembly section")
		}
	}
	return nil
}
