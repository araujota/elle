# APT Repository Setup Guide

This document describes how to set up the self-hosted APT repository for ELLE using aptly.

## Prerequisites

- Ubuntu 22.04+ build machine
- Domain pointing to your server (e.g., apt.elle.dev)
- Web server (nginx/apache)
- GPG key for package signing

## Build Machine Setup

### 1. Install aptly

```bash
# Add aptly repository
wget -qO - https://www.aptly.info/pubkey.txt | sudo apt-key add -
echo "deb http://repo.aptly.info/ squeeze main" | sudo tee /etc/apt/sources.list.d/aptly.list

# Install
sudo apt update
sudo apt install aptly
```

### 2. Create GPG Signing Key

```bash
# Generate a new GPG key for signing
gpg --full-generate-key

# Choose:
# - RSA and RSA
# - 4096 bits
# - Key does not expire
# - Name: ELLE Package Signing Key
# - Email: packages@elle.dev

# List keys to get the key ID
gpg --list-keys

# Export public key for users
gpg --export --armor packages@elle.dev > gpg.key
```

### 3. Initialize aptly Repository

```bash
# Create the repository
aptly repo create -distribution=stable -component=main elle-stable

# Optionally create testing/unstable repos
aptly repo create -distribution=testing -component=main elle-testing
```

### 4. Publish Repository

```bash
# Initial publish (empty repo is fine)
aptly publish repo -distribution=stable elle-stable

# This creates ~/.aptly/public/ with the repository structure
```

## Publishing Packages

### Build the Package

```bash
cd /path/to/elle
make build-deb
```

### Add to Repository

```bash
# Add package to repo
aptly repo add elle-stable build/elle_0.1.0-1_all.deb

# Update published repository
aptly publish update stable
```

### Upload to Server

```bash
# Sync to web server
rsync -avz --delete ~/.aptly/public/ deploy@apt.elle.dev:/var/www/apt/

# Also upload the GPG key
scp gpg.key deploy@apt.elle.dev:/var/www/apt/
```

## Web Server Configuration

### nginx

```nginx
server {
    listen 80;
    listen 443 ssl;
    server_name apt.elle.dev;

    root /var/www/apt;
    autoindex on;

    ssl_certificate /etc/letsencrypt/live/apt.elle.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/apt.elle.dev/privkey.pem;

    location / {
        try_files $uri $uri/ =404;
    }

    # Serve GPG key
    location /gpg.key {
        default_type application/pgp-keys;
    }
}
```

## Client Setup

Users add your repository like this:

```bash
# Download and install GPG key
curl -fsSL https://apt.elle.dev/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/elle-archive-keyring.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/elle-archive-keyring.gpg] https://apt.elle.dev stable main" | sudo tee /etc/apt/sources.list.d/elle.list

# Update and install
sudo apt update
sudo apt install elle
```

## Automation

### GitHub Actions Workflow

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build-and-publish:
    runs-on: ubuntu-24.04

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y devscripts debhelper dh-python

      - name: Build Debian package
        run: make build-deb

      - name: Upload to APT repository
        env:
          APT_DEPLOY_KEY: ${{ secrets.APT_DEPLOY_KEY }}
        run: |
          # Set up SSH key
          mkdir -p ~/.ssh
          echo "$APT_DEPLOY_KEY" > ~/.ssh/id_ed25519
          chmod 600 ~/.ssh/id_ed25519

          # Upload package
          scp build/elle_*.deb deploy@apt.elle.dev:/var/www/apt/incoming/

          # Trigger repository update on server
          ssh deploy@apt.elle.dev "/usr/local/bin/update-apt-repo.sh"
```

### Server-side Update Script

Create `/usr/local/bin/update-apt-repo.sh` on apt.elle.dev:

```bash
#!/bin/bash
set -e

cd /var/www/apt

# Import any new packages
for deb in incoming/*.deb; do
    [ -f "$deb" ] || continue
    aptly repo add elle-stable "$deb"
    mv "$deb" processed/
done

# Update repository
aptly publish update stable
```

## Repository Structure

After publishing, your repository will have this structure:

```
/var/www/apt/
├── gpg.key                    # Public signing key
├── dists/
│   └── stable/
│       ├── InRelease          # Signed release file
│       ├── Release            # Release metadata
│       ├── Release.gpg        # Detached signature
│       └── main/
│           └── binary-all/
│               └── Packages   # Package index
├── pool/
│   └── main/
│       └── e/
│           └── elle/
│               └── elle_0.1.0-1_all.deb
└── incoming/                  # For automation
```

## Troubleshooting

### Key not trusted

Ensure the GPG key is imported correctly:

```bash
# On client
gpg --show-keys /usr/share/keyrings/elle-archive-keyring.gpg
```

### Signature verification failed

Re-sign the repository:

```bash
aptly publish update -force-overwrite stable
```

### Package not found after publish

Check that the package is in the pool:

```bash
aptly repo show -with-packages elle-stable
```
