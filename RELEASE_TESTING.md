# ELLE Release Testing Checklist

Manual testing checklist for ELLE v0.1.0 release.

## Prerequisites

- [ ] Ubuntu 24.04 LTS (or compatible) test environment
- [ ] Ollama installed (for AI feature testing)
- [ ] Docker installed (for container feature testing)
- [ ] Test user with sudo access

---

## 1. Package Installation

### Fresh Install
```bash
# Add repository (replace with actual URL)
echo "deb [signed-by=/path/to/key.gpg] http://apt.elle.dev/ jammy main" | sudo tee /etc/apt/sources.list.d/elle.list
sudo apt update
sudo apt install elle
```

- [ ] Package installs without errors
- [ ] `elle` user created (`getent passwd elle`)
- [ ] `/var/lib/elle` directory exists with correct permissions (750, elle:elle)
- [ ] `/etc/elle` directory exists
- [ ] `/etc/elle/elle.toml` config file created from example
- [ ] `elled.service` enabled (`systemctl is-enabled elled`)
- [ ] Man pages installed (`man elle`, `man elled`)

### Upgrade Install
- [ ] Upgrading from previous version preserves config
- [ ] Services restart correctly after upgrade

### Removal
```bash
sudo apt remove elle      # Keep config
sudo apt purge elle       # Remove everything
```

- [ ] `apt remove` leaves `/etc/elle` and `/var/lib/elle`
- [ ] `apt purge` removes `/etc/elle`, `/var/lib/elle`, and `elle` user

---

## 2. Setup Wizard

### First Run
```bash
elle
```

- [ ] Wizard launches automatically on first run
- [ ] Welcome message displays correctly

### Environment Check
- [ ] Python version detected correctly
- [ ] Config directory status shown
- [ ] **Ollama detection:**
  - [ ] If running: Shows "Ollama is running" with model list
  - [ ] If installed but not running: Shows "installed but not running" with start instructions
  - [ ] If not installed: Shows "not installed" with install link
- [ ] Can continue setup without Ollama (with warning)

### Arrow-Key Navigation (NEW)
- [ ] **Safety Level selection:**
  - [ ] Arrow keys (up/down) navigate options
  - [ ] j/k (vim keys) navigate options
  - [ ] Enter selects highlighted option
  - [ ] Escape cancels
  - [ ] Description shows for selected item

- [ ] **Confirmation Preference selection:**
  - [ ] Same navigation as above works

- [ ] **Privilege Level selection:**
  - [ ] Same navigation works
  - [ ] Warning text shows for passwordless option

- [ ] **Telemetry Sources (multi-select):**
  - [ ] Arrow keys navigate
  - [ ] Space toggles checkbox
  - [ ] Multiple items can be selected
  - [ ] Enter confirms selection
  - [ ] Pre-selected defaults are correct

- [ ] **Features (multi-select):**
  - [ ] Same behavior as telemetry
  - [ ] Auto-learn packages option present

### Config Saving
- [ ] "Save these settings?" confirmation works
- [ ] Config saved to `~/.config/elle/elle.toml`
- [ ] Preferences correctly persisted

### Reconfiguration
```bash
elle
/reconfigure
```

- [ ] Wizard runs again with previous values as defaults
- [ ] Changes are saved correctly

---

## 3. Ollama / LLM Integration

### With Ollama Running
```bash
ollama serve &
elle
```

- [ ] Model detection works
- [ ] Basic question answering works: "what time is it?"
- [ ] System questions work: "how much disk space is free?"

### Without Ollama
- [ ] Graceful error message when Ollama not available
- [ ] Suggests starting Ollama

---

## 4. Core REPL Commands

### Navigation
- [ ] `/help` - Shows help
- [ ] `/status` - Shows system status
- [ ] `/events` - Shows recent events
- [ ] `/clear` - Clears screen
- [ ] `/exit` - Exits REPL
- [ ] Ctrl+C - Interrupts current operation
- [ ] Ctrl+D - Exits REPL

### History
- [ ] Up/Down arrows navigate history
- [ ] Ctrl+R searches history
- [ ] History persists across sessions

### Tab Completion
- [ ] Slash commands complete on Tab
- [ ] Command descriptions shown in completion menu

---

## 5. Package Learning (`/learn`)

### Basic Usage
```bash
elle
/learn curl
```

- [ ] Gathers intelligence from dpkg, completions, man pages
- [ ] Shows extraction progress
- [ ] Generates capabilities
- [ ] Capabilities saved to database

### Learn All
```bash
/learn --all --dry-run
```

- [ ] Lists packages that would be learned
- [ ] Respects `--dry-run` (no actual learning)
- [ ] `--max-concurrent` limits parallel learning

### Edge Cases
- [ ] Non-existent package: graceful error
- [ ] Already learned package: skips or updates

---

## 6. Daemon (`elled`)

### Startup
```bash
sudo systemctl start elled
sudo systemctl status elled
```

- [ ] Daemon starts without errors
- [ ] Runs as `elle` user
- [ ] PID file created
- [ ] Logs to journal (`journalctl -u elled`)

### Telemetry
- [ ] Journal watcher active (check logs)
- [ ] Probes running (disk, memory, network)
- [ ] Events stored in database

### Auto-Learn (NEW)
With `auto_learn_new_packages: true` in config:

```bash
sudo apt install htop  # or any package
```

- [ ] New package detected in daemon logs
- [ ] Capability generation triggered
- [ ] Capabilities saved to autogen.db

### API (if enabled)
```bash
curl http://localhost:8080/health
```

- [ ] API responds
- [ ] OpenAI-compatible endpoints work

---

## 8. Capability System

### Capability Execution
- [ ] Built-in capabilities execute correctly
- [ ] Risk levels enforced
- [ ] Policy checks applied
- [ ] Confirmation prompts for high-risk operations

### Auto-generated Capabilities
- [ ] Can invoke learned capabilities
- [ ] Input validation works
- [ ] Command templates execute correctly

---

## 9. Incident Vault

### Incident Creation
- [ ] Errors create incidents
- [ ] Incidents have fingerprints
- [ ] Similar incidents grouped

### Incident Search
```bash
/incidents
```

- [ ] Lists open incidents
- [ ] Can view incident details

---

## 10. Man Vault

### Search
```bash
/man systemctl
```

- [ ] Returns relevant man page content
- [ ] Hybrid search (FTS + semantic) works

---

## 11. Reactive Functions

### Create Function
```bash
/react create "when disk is 90% full, alert me"
```

- [ ] NL prompt parsed correctly
- [ ] Function created with trigger/condition/actions
- [ ] Saved to database

### List/Manage
```bash
/react list
/react show <id>
/react enable <id>
/react disable <id>
/react delete <id>
```

- [ ] All management commands work

---

## 12. Edge Cases & Error Handling

### Invalid Input
- [ ] Empty input handled gracefully
- [ ] Very long input doesn't crash
- [ ] Special characters handled

### Network Issues
- [ ] Ollama connection timeout handled
- [ ] Graceful degradation without network

### Permission Issues
- [ ] Clear error for permission denied
- [ ] Polkit prompts work correctly

### Resource Limits
- [ ] Large file handling
- [ ] Many concurrent operations

---

## 13. Security

### Denylist
Test that these are blocked:
- [ ] `rm -rf /`
- [ ] `sudo` commands (unless allowed)
- [ ] Fork bombs
- [ ] `curl | bash` patterns

### Config Editing Safety
- [ ] Forbidden paths rejected (`/etc/passwd`, `/etc/shadow`)
- [ ] Backup created before edits
- [ ] Rollback works

### Polkit Integration
- [ ] Password prompt appears for privileged operations
- [ ] Group-based auth works (if configured)

---

## 14. Performance

- [ ] Startup time reasonable (< 2s)
- [ ] Memory usage stable over time
- [ ] No memory leaks in long sessions
- [ ] Database queries fast

---

## 15. Uninstall Verification

```bash
sudo apt purge elle
```

- [ ] All files removed
- [ ] User/group removed
- [ ] No orphaned processes
- [ ] Systemd units removed

---

## Test Environment Notes

| Component | Version | Notes |
|-----------|---------|-------|
| Ubuntu | | |
| Python | | |
| Ollama | | |
| Docker | | |

## Issues Found

| Issue | Severity | Steps to Reproduce | Fixed? |
|-------|----------|-------------------|--------|
| | | | |

---

## Sign-Off

- [ ] All critical tests pass
- [ ] No blocking issues
- [ ] Ready for release

Tested by: _________________
Date: _________________
