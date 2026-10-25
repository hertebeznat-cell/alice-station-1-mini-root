package homebrew

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
)

type Runner struct {
	rt       wazero.Runtime
	module   api.Module
	tick     api.Function
	manifest Manifest
	config   map[string]any
	pending  []byte
	frames   int
	hostErr  error
	Logs     []string
}
type PluginRunner interface {
	Tick(map[string]any, uint32) ([]byte, error)
	Close()
	LogLines() []string
	MemoryBytes() uint32
}

func (r *Runner) LogLines() []string { return append([]string{}, r.Logs...) }
func (r *Runner) MemoryBytes() uint32 {
	if r.module.Memory() != nil {
		return r.module.Memory().Size()
	}
	return 0
}

func NewRunner(p *Package, config map[string]any) (*Runner, error) {
	if e := wasmLimits(p.Module); e != nil {
		return nil, e
	}
	r := &Runner{manifest: p.Manifest, config: config}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	r.rt = wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfigInterpreter().WithMemoryLimitPages(32).WithCloseOnContextDone(true).WithDebugInfoEnabled(false))
	fail := func(err error) (*Runner, error) { r.rt.Close(context.Background()); return nil, err }
	host := r.rt.NewHostModuleBuilder("alice")
	host.NewFunctionBuilder().WithFunc(func(_ context.Context, m api.Module, ptr, n uint32) float64 {
		key, ok := r.read(m, ptr, n, 64)
		if !ok {
			return 0
		}
		v := r.config[key]
		if x, ok := v.(float64); ok {
			return x
		}
		if x, ok := v.(bool); ok && x {
			return 1
		}
		return 0
	}).Export("config_number")
	host.NewFunctionBuilder().WithFunc(func(_ context.Context, m api.Module, ptr, n, out, capacity uint32) uint32 {
		key, ok := r.read(m, ptr, n, 64)
		if !ok {
			return ^uint32(0)
		}
		value, _ := r.config[key].(string)
		if uint32(len(value)) > capacity || capacity > 1024 || m.Memory() == nil || !m.Memory().Write(out, []byte(value)) {
			r.hostErr = errors.New("invalid config string buffer")
			return ^uint32(0)
		}
		return uint32(len(value))
	}).Export("config_string")
	host.NewFunctionBuilder().WithFunc(func(_ context.Context, m api.Module, ptr, n uint32) uint32 {
		if !Has(r.manifest, "led.ring") {
			r.hostErr = errors.New("permission denied: led.ring")
			return 1
		}
		r.frames++
		if n != 36 || r.frames > 1 || m.Memory() == nil {
			r.hostErr = errors.New("one 36-byte LED frame is allowed per tick")
			return 1
		}
		frame, ok := m.Memory().Read(ptr, n)
		if !ok {
			r.hostErr = errors.New("LED buffer is outside WebAssembly memory")
			return 1
		}
		r.pending = append(r.pending[:0], frame...)
		return 0
	}).Export("led_write")
	host.NewFunctionBuilder().WithFunc(func(_ context.Context, m api.Module, ptr, n uint32) {
		if !Has(r.manifest, "log.write") {
			r.hostErr = errors.New("permission denied: log.write")
			return
		}
		s, ok := r.read(m, ptr, n, 1024)
		if !ok {
			return
		}
		r.Logs = append(r.Logs, strings.ToValidUTF8(s, "?"))
		if len(r.Logs) > 32 {
			r.Logs = r.Logs[len(r.Logs)-32:]
		}
	}).Export("log")
	if _, err := host.Instantiate(ctx); err != nil {
		return fail(err)
	}
	// No WASI module, filesystem, environment, sockets or process imports exist.
	module, err := r.rt.InstantiateWithConfig(ctx, p.Module, wazero.NewModuleConfig().WithName(p.Manifest.ID).WithStartFunctions())
	if err != nil {
		return fail(err)
	}
	r.module = module
	r.tick = module.ExportedFunction("alice_tick")
	if r.tick == nil || module.Memory() == nil {
		return fail(errors.New("module must export memory and alice_tick(milliseconds)"))
	}
	def := r.tick.Definition()
	if len(def.ParamTypes()) != 1 || def.ParamTypes()[0] != api.ValueTypeI32 || len(def.ResultTypes()) != 0 {
		return fail(errors.New("alice_tick signature must be (i32) -> ()"))
	}
	if r.hostErr != nil {
		return fail(r.hostErr)
	}
	r.pending = nil
	r.frames = 0
	return r, nil
}
func (r *Runner) read(m api.Module, ptr, n, limit uint32) (string, bool) {
	if n > limit || m.Memory() == nil {
		r.hostErr = errors.New("invalid host string length")
		return "", false
	}
	b, ok := m.Memory().Read(ptr, n)
	if !ok {
		r.hostErr = errors.New("host string is outside WebAssembly memory")
		return "", false
	}
	return string(b), true
}
func (r *Runner) Tick(config map[string]any, now uint32) ([]byte, error) {
	r.config = config
	r.pending = nil
	r.frames = 0
	r.hostErr = nil
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := r.tick.Call(ctx, uint64(now)); err != nil {
		return nil, fmt.Errorf("plugin stopped: %w", err)
	}
	if r.hostErr != nil {
		return nil, r.hostErr
	}
	return append([]byte(nil), r.pending...), nil
}
func (r *Runner) Close() { r.rt.Close(context.Background()) }

// ABI stays small: only the two documented permissions can cause effects.
func ValidateModule(p *Package) error {
	if p.Manifest.Engine == "native" {
		return validateELF(p.Module)
	}
	c, e := Config(p.Manifest, nil)
	if e != nil {
		return e
	}
	r, e := NewRunner(p, c)
	if e != nil {
		return e
	}
	r.Close()
	return nil
}

// Used by the moderator service: validate bytecode and ABI without executing
// a guest start function or tick supplied by an anonymous author.
func ValidateModuleFormat(p *Package) error {
	if p.Manifest.Engine == "native" {
		return validateELF(p.Module)
	}
	if e := wasmLimits(p.Module); e != nil {
		return e
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	rt := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfigInterpreter().WithMemoryLimitPages(32).WithDebugInfoEnabled(false))
	defer rt.Close(context.Background())
	compiled, e := rt.CompileModule(ctx, p.Module)
	if e != nil {
		return e
	}
	defer compiled.Close(context.Background())
	f := compiled.ExportedFunctions()["alice_tick"]
	if f == nil || len(f.ParamTypes()) != 1 || f.ParamTypes()[0] != api.ValueTypeI32 || len(f.ResultTypes()) != 0 {
		return errors.New("alice_tick signature must be (i32) -> ()")
	}
	if compiled.ExportedMemories()["memory"] == nil {
		return errors.New("module must export memory")
	}
	return nil
}
