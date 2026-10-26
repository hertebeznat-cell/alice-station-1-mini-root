package homebrew

import (
 "context"
 "encoding/binary"
 "encoding/json"
 "math"
 "net"
 "sync"
 "time"
)

// Only amplitude/frequency summaries cross loopback. No PCM is stored or served.
const AudioMeterAddress = "127.0.0.1:18789"

type AudioLevels struct {
 Available bool `json:"available"`
 Active bool `json:"active"`
 Source string `json:"source"`
 RMS float64 `json:"rms"`
 Peak float64 `json:"peak"`
 Bands [12]float64 `json:"bands"`
 Updated time.Time `json:"updated_at"`
 Error string `json:"error,omitempty"`
}
type AudioMeter struct { mu sync.Mutex; levels AudioLevels }
func (a *AudioMeter) Snapshot() AudioLevels {
 a.mu.Lock(); defer a.mu.Unlock()
 result := a.levels
 result.Available = !result.Updated.IsZero() && time.Since(result.Updated) < 400*time.Millisecond
 result.Active = result.Available && result.Peak > 0.00001
 if !result.Available { result.RMS=0; result.Peak=0; result.Bands=[12]float64{} }
 return result
}
func (a *AudioMeter) Serve(ctx context.Context) {
 address, _ := net.ResolveUDPAddr("udp4", AudioMeterAddress)
 conn, err := net.ListenUDP("udp4", address)
 if err != nil { a.mu.Lock(); a.levels.Error=err.Error(); a.mu.Unlock(); return }
 defer conn.Close()
 go func(){ <-ctx.Done(); conn.Close() }()
 buffer := make([]byte, 512)
 var last time.Time
 for {
  n, peer, err := conn.ReadFromUDP(buffer)
  if err != nil { return }
  if string(buffer[:n]) == "ALICE_LEVELS1" {
   payload, _ := json.Marshal(a.Snapshot())
   conn.WriteToUDP(payload, peer)
   continue
  }
  if n != 64 || string(buffer[:8]) != "ALICEAU1" || time.Since(last)<8*time.Millisecond { continue }
  numbers := [14]float64{}
  valid := true
  for i := range numbers {
   x := float64(math.Float32frombits(binary.LittleEndian.Uint32(buffer[8+i*4:])))
   if math.IsNaN(x)||math.IsInf(x,0)||x<0||x>1 { valid=false; break }; numbers[i]=x
  }
  if !valid { continue }
  last=time.Now()
  a.mu.Lock(); a.levels=AudioLevels{Source:"airplay",RMS:numbers[0],Peak:numbers[1],Updated:last}
  copy(a.levels.Bands[:],numbers[2:]); a.mu.Unlock()
 }
}
