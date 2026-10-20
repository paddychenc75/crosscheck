package example

import (
	"time"

	"internal/rpc"
)

func Load(c *rpc.Client) error {
	_, err := c.Call("order.Get", nil, rpc.WithTimeout(2*time.Second))
	return err
}
