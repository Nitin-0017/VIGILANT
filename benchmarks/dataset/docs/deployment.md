# Deployment

## HTTP calls

`requests.get` has no timeout by default, so always pass one.

## Running tests

Run `pytest` before opening a pull request, and run the linters too.

## Health checks

The load balancer calls `/healthz` every ten seconds.

## CDN

Static assets are cached at the network edge for one hour.

## Scaling

Add more web processes when average response time passes 500 milliseconds.
