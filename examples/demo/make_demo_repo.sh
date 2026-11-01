#!/bin/sh
# Create a small Git repository with four documented changes for a VIGILANT demo.
# Usage: examples/demo/make_demo_repo.sh DIRECTORY
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 DIRECTORY" >&2
  exit 2
fi
if [ -e "$1" ]; then
  echo "$1 already exists; choose a new directory" >&2
  exit 1
fi

mkdir -p "$1"
cd "$1"
git init --quiet --initial-branch=main

commit() {
  git add .
  git -c user.name="VIGILANT demo" -c user.email="demo@example.invalid" \
    -c commit.gpgsign=false commit --quiet -m "$1"
}

cat > client.py <<'CODE'
def connect(host, timeout=30):
    return open_socket(host, timeout)


def send_invoice(customer, amount):
    return post_invoice(customer, amount)


def normalize_email(address):
    return address.strip().lower()


def sync_inventory(warehouse):
    return run_sync(warehouse)
CODE

cat > README.md <<'DOC'
# Client

## Connecting

`connect` opens a socket to the host. The default timeout is 30 seconds.

## Email addresses

`normalize_email` removes surrounding whitespace and converts the address to lowercase.

## Inventory

`sync_inventory` keeps stock levels up to date for a warehouse.

## Office hours

The support office opens at 9:00 on weekdays.
DOC
commit "Initial client"

cat > client.py <<'CODE'
def connect(host, timeout=90):
    return open_socket(host, timeout)


def send_invoice(customer, amount, currency="USD"):
    return post_invoice(customer, amount, currency)


def normalize_email(address):
    cleaned = address.strip()
    return cleaned.lower()


def sync_inventory(warehouse, dry_run=False):
    return run_sync(warehouse, dry_run)
CODE
commit "Update client"

echo "Demo repository created in $1"
