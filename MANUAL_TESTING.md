# ELLE Agentic Question Answering - Manual Testing Guide

This document provides test cases for manually testing the agentic question answering system.

## Prerequisites

1. Ensure Ollama is running with a suitable model
2. Have some services running (nginx, ssh, postgresql, etc.)
3. Have Docker installed with some containers (optional)

## Test Categories

---

## 1. Service Status Questions

These questions should trigger the `service.status` capability.

### Basic Status Queries

```
what is the status of ssh?
is nginx running?
check sshd status
show me nginx status
is postgresql service running?
status of docker
```

### Expected Behavior
- Should execute `service.status` capability
- Should return actual service state (active/inactive/failed)
- Should include PID if service is running
- Should show evidence attribution

### Example Output
```
ssh (OpenBSD Secure Shell server) is active (running). PID: 1234.

[Evidence: service.status]
Try: Check service logs with 'show ssh logs'
```

---

## 2. Service Log Questions

These questions should trigger the `service.logs` capability.

### Log Queries

```
show me nginx logs
get ssh service logs
nginx logs
show postgresql logs
```

### Expected Behavior
- Should execute `service.logs` capability
- Should return recent journal entries
- Should include timestamps

---

## 3. File Content Questions

These questions should trigger the `file.read` capability.

### File Queries

```
what's in /etc/hosts?
show me /etc/hostname
read /etc/os-release
cat /etc/passwd
display /etc/resolv.conf
what is in /etc/fstab?
```

### Expected Behavior
- Should execute `file.read` capability
- Should return file contents
- Should handle file not found gracefully

---

## 4. Package Information Questions

These questions should trigger the `package.info` capability.

### Package Queries

```
is docker installed?
is nginx installed?
what version of python3 do I have?
python3 version?
is git installed?
show me curl package info
```

### Expected Behavior
- Should execute `package.info` capability
- Should return installed status and version
- Should handle not-installed packages

### Example Output
```
nginx is installed (version 1.24.0-1ubuntu1).

[Evidence: package.info]
Try: Install the package with '/do install nginx'
```

---

## 5. Docker Container Questions

These questions should trigger docker capabilities.

### Container List Queries

```
what containers are running?
docker containers
show me all containers
list docker containers
```

### Container Status Queries

```
status of the postgres container
myapp container status
is the redis container running?
```

### Container Log Queries

```
show postgres container logs
get myapp logs
docker logs for redis
```

### Expected Behavior
- Should execute appropriate docker capability
- Should list containers with status
- Should handle Docker not installed gracefully

---

## 6. Network Questions

These questions should trigger network capabilities.

### Listener Queries

```
what's listening on the ports?
show me listening ports
what ports are open?
show all listening services
```

### Port-Specific Queries

```
what's on port 80?
who is using port 8080?
what's listening on port 443?
port 22 status
```

### Expected Behavior
- Should execute `network.listeners` capability
- Should show process names and ports
- Should include protocol (tcp/udp)

### Example Output
```
Listening ports:
State    Recv-Q   Send-Q   Local Address:Port   Peer Address:Port   Process
LISTEN   0        128      0.0.0.0:22           0.0.0.0:*           sshd
LISTEN   0        511      0.0.0.0:80           0.0.0.0:*           nginx

[Evidence: network.listeners]
```

---

## 7. System Information Questions

These questions should trigger system capabilities.

### System Info Queries

```
show me system info
what system is this?
system information
```

### Resource Queries

```
how much memory is being used?
disk usage
system resources
cpu usage
show memory usage
how much disk space is left?
system load
uptime
```

### Expected Behavior
- Should execute appropriate system capability
- Should show memory/disk/CPU information
- Should be human-readable

---

## 8. Fallback to LLM Questions

These questions should NOT trigger agentic handling and should fall back to pure LLM.

### General Questions (No System State)

```
what is Linux?
how do I create a bash script?
explain the difference between TCP and UDP
what does chmod do?
how do I set up a cron job?
```

### Expected Behavior
- Should NOT show evidence attribution
- Should use pure LLM response
- Should provide helpful explanations

---

## 9. Edge Cases

### Nonexistent Services

```
is fakeservice123 running?
status of nonexistent-daemon
```

### Expected Behavior
- Should handle gracefully
- Should report service not found or inactive

### Permission Issues

```
what's in /etc/shadow?
read /root/.bashrc
```

### Expected Behavior
- Should report permission denied
- Should not crash

### Invalid Paths

```
what's in /this/path/does/not/exist?
```

### Expected Behavior
- Should report file not found
- Should suggest alternatives or ask for clarification

---

## 10. Combined/Complex Questions

These may require multiple capabilities or may not be fully supported yet.

```
is nginx running and what port is it on?
show nginx status and recent errors
```

### Expected Behavior
- May only answer part of the question
- Should indicate if some information is missing

---

## Test Flow Checklist

### Quick Smoke Test

1. [ ] `is ssh running?` - Should show service status
2. [ ] `what's in /etc/hostname?` - Should show file content
3. [ ] `is git installed?` - Should show package info
4. [ ] `what ports are open?` - Should list listeners
5. [ ] `how much memory is being used?` - Should show resources

### Full Test Suite

#### Services
- [ ] Status query for running service
- [ ] Status query for stopped service
- [ ] Status query for nonexistent service
- [ ] Log query for service with logs
- [ ] Log query for service without logs

#### Files
- [ ] Read existing file
- [ ] Read nonexistent file
- [ ] Read file without permission

#### Packages
- [ ] Query installed package
- [ ] Query not-installed package
- [ ] Query version of package

#### Docker (if available)
- [ ] List containers
- [ ] Status of specific container
- [ ] Logs of container

#### Network
- [ ] List all listeners
- [ ] Query specific port (used)
- [ ] Query specific port (unused)

#### System
- [ ] System info
- [ ] Memory usage
- [ ] Disk usage

#### Fallback
- [ ] General Linux question (should use LLM only)

---

## Troubleshooting

### No Evidence Shown

If questions aren't being handled agentically:
1. Check if the question matches a known pattern
2. Try rephrasing using keywords like "status", "running", "installed"
3. Check logs for pattern matching failures

### Capability Execution Fails

If capabilities fail to execute:
1. Check if the required tools are installed (systemctl, dpkg, docker, ss)
2. Check permissions
3. Check daemon connectivity (if using daemon capabilities)

### LLM Not Available

If seeing "LLM not available":
1. Ensure Ollama is running: `systemctl status ollama`
2. Check model is loaded: `ollama list`
3. Try warming up the model: `ollama run qwen2.5:7b-instruct-q8_0 "hello"`

---

## Reporting Issues

When reporting issues, include:
1. The exact question asked
2. The response received
3. What you expected
4. System details (Ubuntu version, services available)
5. Any error messages from logs
