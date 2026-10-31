# Cache

## Overview

The application keeps frequently read values in memory to reduce database load.

## Expiry

Entries disappear from memory five minutes after they are written.

## Eviction

When memory is full, the least recently used values are thrown away first.

## Warming

Popular product pages are loaded in the background after every deployment.

## Using the cache

`cache_get(key)` returns a stored value or `None`.
