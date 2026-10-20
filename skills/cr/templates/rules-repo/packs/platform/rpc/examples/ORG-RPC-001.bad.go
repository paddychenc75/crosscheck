package example

import "internal/rpc"

func Load(c *rpc.Client) error {
	_, err := c.Call("order.Get", nil)
	return err
}
