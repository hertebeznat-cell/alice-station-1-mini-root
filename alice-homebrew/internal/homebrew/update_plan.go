package homebrew

import "time"

type UpdatePlan struct {
	Target          string    `json:"target"`
	Data            string    `json:"data"`
	Args            []string  `json:"args"`
	PID             int       `json:"pid"`
	ProcessStart    string    `json:"process_start"`
	Version         string    `json:"version"`
	PreviousVersion string    `json:"previous_version"`
	SHA256          string    `json:"sha256"`
	PreviousSHA256  string    `json:"previous_sha256"`
	StateSHA256     string    `json:"state_sha256,omitempty"`
	HealthURL       string    `json:"health_url"`
	Phase           string    `json:"phase"`
	Created         time.Time `json:"created"`
}
