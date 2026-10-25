package hardware

import (
	"bytes"
	"encoding/binary"
	"io"
	"testing"
)

func TestReadInputARM64(t *testing.T) {
	b := make([]byte, 24)
	binary.LittleEndian.PutUint64(b, 100)
	binary.LittleEndian.PutUint16(b[16:], 3)
	binary.LittleEndian.PutUint16(b[18:], 25)
	binary.LittleEndian.PutUint32(b[20:], 0xffffffff)
	e, err := ReadInput(bytes.NewReader(b))
	if err != nil || e.Type != 3 || e.Code != 25 || e.Value != -1 || e.Seconds != 100 {
		t.Fatalf("%+v %v", e, err)
	}
	if _, e := ReadInput(bytes.NewReader(b[:23])); e != io.ErrUnexpectedEOF {
		t.Fatal(e)
	}
}
