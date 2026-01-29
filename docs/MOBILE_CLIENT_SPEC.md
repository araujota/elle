# ELLE Mobile Client Specification

## Overview

The ELLE Mobile Client is an uber-lightweight, cross-platform mobile application built with Expo that provides secure remote access to ELLE instances. It enables users to manage multiple ELLE installations from a single unified interface through natural language interaction.

### Design Principles

1. **Multi-Instance Management** - Scan and manage multiple ELLE instances across different machines
2. **Natural Language First** - Pure conversational interface for all interactions
3. **Rich Content Display** - Stylized components for diffs, plans, code blocks, and approvals
4. **Remote Configuration** - Full configuration management capabilities from mobile
5. **Minimal Footprint** - Lightweight, fast, battery-efficient

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ELLE Mobile Client                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                   App Shell                          │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐             │   │
│  │  │ Machines│  │  Chat   │  │Settings │             │   │
│  │  │  List   │  │ Screen  │  │ Screen  │             │   │
│  │  └─────────┘  └─────────┘  └─────────┘             │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Core Services Layer                     │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐            │   │
│  │  │Connection│ │  Cert    │ │  Config  │            │   │
│  │  │ Manager  │ │  Store   │ │  Sync    │            │   │
│  │  └──────────┘ └──────────┘ └──────────┘            │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Secure Storage Layer                    │   │
│  │         (expo-secure-store / Keychain)              │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ mTLS (per-instance certs)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ELLE Instance 1          ELLE Instance 2          ...      │
│  (home-server)            (work-laptop)                     │
│  └─ Mobile Gateway        └─ Mobile Gateway                 │
│     :8378                    :8378                          │
└─────────────────────────────────────────────────────────────┘
```

### State Management

```
┌─────────────────────────────────────────┐
│            Zustand Store                │
├─────────────────────────────────────────┤
│ machines: Machine[]                     │
│ activeMachineId: string | null          │
│ conversations: Map<machineId, Message[]>│
│ pendingApprovals: Approval[]            │
│ connectionStatus: Map<machineId, Status>│
└─────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Rationale |
|-------|------------|-----------|
| Framework | Expo SDK 52+ | Cross-platform, OTA updates, managed workflow |
| Language | TypeScript | Type safety, better DX |
| Navigation | Expo Router | File-based routing, deep linking |
| State | Zustand | Lightweight, TypeScript-friendly |
| Storage | expo-secure-store | Keychain/Keystore for certs |
| Networking | Custom fetch with mTLS | Native TLS client cert support |
| QR Scanning | expo-camera | Built-in barcode scanning |
| UI Components | React Native + Custom | Minimal dependencies |
| Markdown | react-native-markdown-display | Chat message rendering |
| Syntax Highlighting | Custom (lightweight) | Diff and code display |

### Dependencies (Minimal)

```json
{
  "dependencies": {
    "expo": "~52.0.0",
    "expo-camera": "~15.0.0",
    "expo-secure-store": "~13.0.0",
    "expo-router": "~4.0.0",
    "zustand": "^4.5.0",
    "react-native-markdown-display": "^7.0.0"
  }
}
```

---

## Data Models

### Core Types

```typescript
// Machine (ELLE Instance)
interface Machine {
  id: string;                    // UUID
  name: string;                  // User-assigned name
  host: string;                  // Gateway host/IP
  port: number;                  // Gateway port (default 8378)
  deviceId: string;              // Device ID from pairing
  fingerprint: string;           // Server cert fingerprint (pinning)
  pairedAt: Date;
  lastConnectedAt: Date | null;
  status: 'connected' | 'disconnected' | 'error';
  role: 'mobile_readonly' | 'mobile_operator';
  elevation?: {
    role: string;
    expiresAt: Date;
  };
}

// Credentials (stored securely)
interface MachineCredentials {
  machineId: string;
  clientCertPem: string;
  clientKeyPem: string;
  caCertPem: string;
}

// Chat Message
interface Message {
  id: string;
  machineId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  metadata?: {
    model?: string;
    tokens?: number;
    executionMode?: string;
  };
  // Rich content blocks embedded in message
  blocks?: ContentBlock[];
}

// Content Blocks (rich display elements)
type ContentBlock =
  | DiffBlock
  | PlanBlock
  | CodeBlock
  | ApprovalBlock
  | ConfigBlock
  | ErrorBlock;

interface DiffBlock {
  type: 'diff';
  filename: string;
  hunks: DiffHunk[];
  stats: { additions: number; deletions: number };
}

interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

interface DiffLine {
  type: 'context' | 'addition' | 'deletion';
  content: string;
  lineNumber?: { old?: number; new?: number };
}

interface PlanBlock {
  type: 'plan';
  title: string;
  steps: PlanStep[];
  risks: string[];
  requiresElevation: boolean;
}

interface PlanStep {
  index: number;
  description: string;
  command?: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
}

interface CodeBlock {
  type: 'code';
  language: string;
  content: string;
  filename?: string;
}

interface ApprovalBlock {
  type: 'approval';
  id: string;
  title: string;
  description: string;
  action: 'execute' | 'apply_config' | 'elevate';
  payload: unknown;
  status: 'pending' | 'approved' | 'denied';
}

interface ConfigBlock {
  type: 'config';
  section: string;
  current: Record<string, unknown>;
  proposed?: Record<string, unknown>;
}

interface ErrorBlock {
  type: 'error';
  code: string;
  message: string;
  suggestion?: string;
}

// QR Payload (from gateway)
interface QRPayload {
  version: number;
  host: string;
  port: number;
  token: string;
  server_fingerprint: string;
}

// Configuration schemas
interface ElleConfig {
  daemon: DaemonConfig;
  llm: LLMConfig;
  mobile: MobileConfig;
  reactive: ReactiveConfig;
}

interface DaemonConfig {
  telemetry_enabled: boolean;
  event_retention_days: number;
  manvault_auto_index: boolean;
  capability_bootstrap_enabled: boolean;
  auto_learn_new_packages: boolean;
}

interface LLMConfig {
  ollama_host: string;
  llm_model: string;
  embedding_model: string;
  context_window: number;
}

interface MobileConfig {
  enabled: boolean;
  bind_host: string;
  bind_port: number;
  max_paired_devices: number;
  default_role: string;
}
```

---

## Screen Structure

```
app/
├── _layout.tsx              # Root layout with providers
├── index.tsx                # Redirect to machines or onboarding
├── (tabs)/
│   ├── _layout.tsx          # Tab navigator
│   ├── machines.tsx         # Machine list screen
│   ├── chat.tsx             # Active chat screen
│   └── settings.tsx         # App settings
├── scan.tsx                 # QR scanner (modal)
├── machine/
│   ├── [id]/
│   │   ├── index.tsx        # Machine detail
│   │   ├── config.tsx       # Remote configuration
│   │   └── history.tsx      # Conversation history
└── onboarding.tsx           # First-time setup
```

### Screen Specifications

#### 1. Machines List (`/machines`)

Primary landing screen showing all paired ELLE instances.

```
┌─────────────────────────────────┐
│  ELLE                      [+]  │  ← Scan button
├─────────────────────────────────┤
│ ┌─────────────────────────────┐ │
│ │ 🟢 home-server              │ │  ← Connected
│ │    192.168.1.100:8378       │ │
│ │    Last: 2 min ago          │ │
│ └─────────────────────────────┘ │
│ ┌─────────────────────────────┐ │
│ │ 🔴 work-laptop              │ │  ← Disconnected
│ │    10.0.0.50:8378           │ │
│ │    Last: 3 days ago         │ │
│ └─────────────────────────────┘ │
│ ┌─────────────────────────────┐ │
│ │ 🟡 dev-box                  │ │  ← Elevated
│ │    192.168.1.200:8378       │ │
│ │    Elevated: 8 min left     │ │
│ └─────────────────────────────┘ │
│                                 │
│                                 │
└─────────────────────────────────┘
```

**Interactions:**
- Tap machine → Navigate to chat
- Long press → Context menu (rename, forget, view details)
- Pull to refresh → Check connection status
- [+] button → Open QR scanner

#### 2. Chat Screen (`/chat`)

Full-screen natural language interface with active machine.

```
┌─────────────────────────────────┐
│ ← home-server        [⚙] [🔒]  │  ← Config, elevation status
├─────────────────────────────────┤
│                                 │
│ ┌─────────────────────────────┐ │
│ │ You                         │ │
│ │ Check if nginx is running   │ │
│ │ and show me its config      │ │
│ └─────────────────────────────┘ │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ ELLE                        │ │
│ │ nginx is active (running).  │ │
│ │                             │ │
│ │ ┌─────────────────────────┐ │ │
│ │ │ 📄 /etc/nginx/nginx.conf│ │ │  ← Code block
│ │ │ ```nginx                │ │ │
│ │ │ worker_processes auto;  │ │ │
│ │ │ events {                │ │ │
│ │ │   worker_connections... │ │ │
│ │ └─────────────────────────┘ │ │
│ └─────────────────────────────┘ │
│                                 │
├─────────────────────────────────┤
│ [  Ask ELLE anything...     ] ⬆│  ← Input
└─────────────────────────────────┘
```

**Rich Content Examples:**

```
┌─────────────────────────────────┐
│ ELLE                            │
│ I'll update the SSH config.     │
│ Here's what will change:        │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ 📝 /etc/ssh/sshd_config     │ │  ← Diff block
│ │ ─────────────────────────── │ │
│ │   #PermitRootLogin yes      │ │
│ │ - PermitRootLogin yes       │ │  ← Red
│ │ + PermitRootLogin no        │ │  ← Green
│ │   #StrictModes yes          │ │
│ │ ─────────────────────────── │ │
│ │ +1 -1                       │ │
│ └─────────────────────────────┘ │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ ⚠️  Requires Confirmation    │ │  ← Approval block
│ │                             │ │
│ │ Apply this configuration    │ │
│ │ change?                     │ │
│ │                             │ │
│ │ [  Deny  ]    [ Approve ]   │ │
│ └─────────────────────────────┘ │
└─────────────────────────────────┘
```

```
┌─────────────────────────────────┐
│ ELLE                            │
│ Here's my plan to set up the    │
│ WireGuard VPN:                  │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ 📋 Execution Plan            │ │  ← Plan block
│ │ ─────────────────────────── │ │
│ │ 1. ☐ Install wireguard pkg  │ │
│ │ 2. ☐ Generate keypair       │ │
│ │ 3. ☐ Create wg0.conf        │ │
│ │ 4. ☐ Enable wg-quick@wg0    │ │
│ │ 5. ☐ Configure firewall     │ │
│ │ ─────────────────────────── │ │
│ │ ⚠️  Risks:                   │ │
│ │ • Network connectivity may  │ │
│ │   be briefly interrupted    │ │
│ │ ─────────────────────────── │ │
│ │ 🔒 Requires elevation       │ │
│ │                             │ │
│ │ [  Cancel  ]  [ Execute ]   │ │
│ └─────────────────────────────┘ │
└─────────────────────────────────┘
```

#### 3. Remote Configuration (`/machine/[id]/config`)

Menu-driven configuration management.

```
┌─────────────────────────────────┐
│ ← Configuration                 │
├─────────────────────────────────┤
│                                 │
│ DAEMON                          │
│ ┌─────────────────────────────┐ │
│ │ Telemetry          [====]   │ │  ← Toggle
│ │ Event Retention    [30] days│ │  ← Number input
│ │ Auto-index Man     [====]   │ │
│ │ Bootstrap Caps     [====]   │ │
│ │ Auto-learn Pkgs    [====]   │ │
│ └─────────────────────────────┘ │
│                                 │
│ LLM                             │
│ ┌─────────────────────────────┐ │
│ │ Ollama Host                 │ │
│ │ [http://127.0.0.1:11434  ]  │ │
│ │                             │ │
│ │ LLM Model                   │ │
│ │ [qwen2.5:7b-instruct-q8▾]   │ │  ← Picker
│ │                             │ │
│ │ Context Window    [32768]   │ │
│ └─────────────────────────────┘ │
│                                 │
│ MOBILE                          │
│ ┌─────────────────────────────┐ │
│ │ Max Paired Devices  [10]    │ │
│ │ Default Role                │ │
│ │ [mobile_readonly         ▾] │ │
│ └─────────────────────────────┘ │
│                                 │
│ ┌─────────────────────────────┐ │
│ │     [ Save Configuration ]  │ │
│ └─────────────────────────────┘ │
│                                 │
│ Changes will be applied to      │
│ home-server immediately.        │
│                                 │
└─────────────────────────────────┘
```

#### 4. QR Scanner (`/scan`)

Full-screen camera with QR detection overlay.

```
┌─────────────────────────────────┐
│ ✕                    Scan QR    │
├─────────────────────────────────┤
│                                 │
│                                 │
│      ┌─────────────────┐        │
│      │                 │        │
│      │    [  QR  ]     │        │  ← Viewfinder
│      │                 │        │
│      └─────────────────┘        │
│                                 │
│                                 │
│  Point camera at the QR code    │
│  displayed by `elle mobile up`  │
│                                 │
└─────────────────────────────────┘
```

**Post-Scan Flow:**

```
┌─────────────────────────────────┐
│         Pair with ELLE?         │
├─────────────────────────────────┤
│                                 │
│  ┌─────────────────────────────┐│
│  │ 🖥️  New ELLE Instance       ││
│  │                             ││
│  │ Host: 192.168.1.100        ││
│  │ Port: 8378                  ││
│  │                             ││
│  │ Fingerprint:                ││
│  │ a3:b4:c5:d6:e7:f8:...      ││
│  └─────────────────────────────┘│
│                                 │
│  Name this machine:             │
│  ┌─────────────────────────────┐│
│  │ home-server                 ││
│  └─────────────────────────────┘│
│                                 │
│  ┌─────────────────────────────┐│
│  │         [ Cancel ]          ││
│  │         [ Pair   ]          ││
│  └─────────────────────────────┘│
│                                 │
└─────────────────────────────────┘
```

---

## API Integration

### Gateway Client

```typescript
// lib/gateway-client.ts

import * as SecureStore from 'expo-secure-store';

interface GatewayClientConfig {
  host: string;
  port: number;
  credentials: MachineCredentials;
}

class GatewayClient {
  private config: GatewayClientConfig;
  private abortController: AbortController | null = null;

  constructor(config: GatewayClientConfig) {
    this.config = config;
  }

  private get baseUrl(): string {
    return `https://${this.config.host}:${this.config.port}`;
  }

  /**
   * Perform mTLS-authenticated fetch.
   *
   * Note: React Native's fetch doesn't natively support client certs.
   * We need a native module or use a library like react-native-ssl-pinning.
   * For Expo, we may need a custom native module or config plugin.
   */
  private async fetch(
    path: string,
    options: RequestInit = {}
  ): Promise<Response> {
    // Implementation depends on native mTLS support
    // See "Native mTLS Module" section below
    return await mtlsFetch(`${this.baseUrl}${path}`, {
      ...options,
      clientCert: this.config.credentials.clientCertPem,
      clientKey: this.config.credentials.clientKeyPem,
      caCert: this.config.credentials.caCertPem,
    });
  }

  /**
   * Send a chat completion request.
   */
  async chat(
    messages: Array<{ role: string; content: string }>,
    options: {
      stream?: boolean;
      onChunk?: (chunk: string) => void;
    } = {}
  ): Promise<string> {
    const response = await this.fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'elle',
        messages,
        stream: options.stream ?? true,
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new GatewayError(error.error, error.code);
    }

    if (options.stream && options.onChunk) {
      return await this.handleStream(response, options.onChunk);
    }

    const result = await response.json();
    return result.choices[0].message.content;
  }

  /**
   * Handle SSE streaming response.
   */
  private async handleStream(
    response: Response,
    onChunk: (chunk: string) => void
  ): Promise<string> {
    const reader = response.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let fullContent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value);
      const lines = text.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') continue;

          try {
            const parsed = JSON.parse(data);
            const content = parsed.choices?.[0]?.delta?.content;
            if (content) {
              fullContent += content;
              onChunk(content);
            }
          } catch {
            // Skip malformed chunks
          }
        }
      }
    }

    return fullContent;
  }

  /**
   * List available models.
   */
  async listModels(): Promise<Array<{ id: string; name: string }>> {
    const response = await this.fetch('/v1/models');
    const result = await response.json();
    return result.data;
  }

  /**
   * Get current configuration.
   */
  async getConfig(): Promise<ElleConfig> {
    const response = await this.fetch('/v1/config');
    return await response.json();
  }

  /**
   * Update configuration.
   */
  async updateConfig(config: Partial<ElleConfig>): Promise<void> {
    const response = await this.fetch('/v1/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new GatewayError(error.error, error.code);
    }
  }

  /**
   * Check health/connectivity.
   */
  async health(): Promise<{ status: string; internal_api: string }> {
    const response = await this.fetch('/health');
    return await response.json();
  }

  /**
   * Cancel ongoing request.
   */
  cancel(): void {
    this.abortController?.abort();
  }
}

class GatewayError extends Error {
  code: string;

  constructor(message: string, code: string) {
    super(message);
    this.code = code;
    this.name = 'GatewayError';
  }
}
```

### Pairing Service

```typescript
// lib/pairing-service.ts

interface PairingResult {
  machine: Machine;
  credentials: MachineCredentials;
}

async function completePairing(
  payload: QRPayload,
  deviceName: string
): Promise<PairingResult> {
  // First request without mTLS (pairing endpoint is public)
  const response = await fetch(
    `https://${payload.host}:${payload.port}/pair`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: payload.token,
        device_name: deviceName,
      }),
      // Pin to expected server fingerprint
      sslPinning: {
        certs: ['sha256/' + payload.server_fingerprint],
      },
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new PairingError(error.detail || 'Pairing failed');
  }

  const result = await response.json();

  const machine: Machine = {
    id: generateUUID(),
    name: deviceName,
    host: payload.host,
    port: payload.port,
    deviceId: result.device_id,
    fingerprint: payload.server_fingerprint,
    pairedAt: new Date(),
    lastConnectedAt: null,
    status: 'disconnected',
    role: 'mobile_readonly',
  };

  const credentials: MachineCredentials = {
    machineId: machine.id,
    clientCertPem: result.client_cert_pem,
    clientKeyPem: result.client_key_pem,
    caCertPem: result.ca_cert_pem,
  };

  return { machine, credentials };
}

function parseQRPayload(data: string): QRPayload {
  try {
    const parsed = JSON.parse(data);

    if (parsed.version !== 1) {
      throw new Error(`Unsupported QR version: ${parsed.version}`);
    }

    return {
      version: parsed.version,
      host: parsed.host,
      port: parsed.port,
      token: parsed.token,
      server_fingerprint: parsed.server_fingerprint,
    };
  } catch (e) {
    throw new PairingError('Invalid QR code format');
  }
}
```

---

## Secure Storage

### Credential Management

```typescript
// lib/credential-store.ts

import * as SecureStore from 'expo-secure-store';

const MACHINES_KEY = 'elle_machines';
const CREDS_PREFIX = 'elle_creds_';

/**
 * Store machine credentials securely.
 */
async function storeCredentials(
  credentials: MachineCredentials
): Promise<void> {
  await SecureStore.setItemAsync(
    CREDS_PREFIX + credentials.machineId,
    JSON.stringify(credentials),
    {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    }
  );
}

/**
 * Retrieve machine credentials.
 */
async function getCredentials(
  machineId: string
): Promise<MachineCredentials | null> {
  const data = await SecureStore.getItemAsync(CREDS_PREFIX + machineId);
  return data ? JSON.parse(data) : null;
}

/**
 * Delete machine credentials.
 */
async function deleteCredentials(machineId: string): Promise<void> {
  await SecureStore.deleteItemAsync(CREDS_PREFIX + machineId);
}

/**
 * Store machine list (non-sensitive metadata only).
 */
async function storeMachines(machines: Machine[]): Promise<void> {
  await SecureStore.setItemAsync(MACHINES_KEY, JSON.stringify(machines));
}

/**
 * Retrieve machine list.
 */
async function getMachines(): Promise<Machine[]> {
  const data = await SecureStore.getItemAsync(MACHINES_KEY);
  return data ? JSON.parse(data) : [];
}
```

---

## State Management

### Zustand Store

```typescript
// store/index.ts

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';

interface AppState {
  // Machines
  machines: Machine[];
  activeMachineId: string | null;

  // Conversations (per machine)
  conversations: Record<string, Message[]>;

  // Connection status
  connectionStatus: Record<string, 'connected' | 'disconnected' | 'connecting' | 'error'>;

  // Pending approvals
  pendingApprovals: ApprovalBlock[];

  // Actions
  addMachine: (machine: Machine) => void;
  removeMachine: (id: string) => void;
  updateMachine: (id: string, updates: Partial<Machine>) => void;
  setActiveMachine: (id: string | null) => void;

  addMessage: (machineId: string, message: Message) => void;
  updateMessage: (machineId: string, messageId: string, updates: Partial<Message>) => void;
  clearConversation: (machineId: string) => void;

  setConnectionStatus: (machineId: string, status: ConnectionStatus) => void;

  addApproval: (approval: ApprovalBlock) => void;
  resolveApproval: (id: string, approved: boolean) => void;
}

export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
      machines: [],
      activeMachineId: null,
      conversations: {},
      connectionStatus: {},
      pendingApprovals: [],

      addMachine: (machine) =>
        set((state) => ({
          machines: [...state.machines, machine],
          conversations: { ...state.conversations, [machine.id]: [] },
        })),

      removeMachine: (id) =>
        set((state) => ({
          machines: state.machines.filter((m) => m.id !== id),
          conversations: Object.fromEntries(
            Object.entries(state.conversations).filter(([k]) => k !== id)
          ),
          activeMachineId:
            state.activeMachineId === id ? null : state.activeMachineId,
        })),

      updateMachine: (id, updates) =>
        set((state) => ({
          machines: state.machines.map((m) =>
            m.id === id ? { ...m, ...updates } : m
          ),
        })),

      setActiveMachine: (id) => set({ activeMachineId: id }),

      addMessage: (machineId, message) =>
        set((state) => ({
          conversations: {
            ...state.conversations,
            [machineId]: [...(state.conversations[machineId] || []), message],
          },
        })),

      updateMessage: (machineId, messageId, updates) =>
        set((state) => ({
          conversations: {
            ...state.conversations,
            [machineId]: (state.conversations[machineId] || []).map((m) =>
              m.id === messageId ? { ...m, ...updates } : m
            ),
          },
        })),

      clearConversation: (machineId) =>
        set((state) => ({
          conversations: { ...state.conversations, [machineId]: [] },
        })),

      setConnectionStatus: (machineId, status) =>
        set((state) => ({
          connectionStatus: { ...state.connectionStatus, [machineId]: status },
        })),

      addApproval: (approval) =>
        set((state) => ({
          pendingApprovals: [...state.pendingApprovals, approval],
        })),

      resolveApproval: (id, approved) =>
        set((state) => ({
          pendingApprovals: state.pendingApprovals.map((a) =>
            a.id === id ? { ...a, status: approved ? 'approved' : 'denied' } : a
          ),
        })),
    }),
    {
      name: 'elle-mobile-storage',
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({
        machines: state.machines,
        conversations: state.conversations,
      }),
    }
  )
);
```

---

## UI Components

### Content Block Renderer

```typescript
// components/ContentBlockRenderer.tsx

import React from 'react';
import { View } from 'react-native';
import { DiffView } from './blocks/DiffView';
import { PlanView } from './blocks/PlanView';
import { CodeView } from './blocks/CodeView';
import { ApprovalView } from './blocks/ApprovalView';
import { ConfigView } from './blocks/ConfigView';
import { ErrorView } from './blocks/ErrorView';

interface Props {
  block: ContentBlock;
  onApprove?: (id: string) => void;
  onDeny?: (id: string) => void;
}

export function ContentBlockRenderer({ block, onApprove, onDeny }: Props) {
  switch (block.type) {
    case 'diff':
      return <DiffView diff={block} />;
    case 'plan':
      return <PlanView plan={block} />;
    case 'code':
      return <CodeView code={block} />;
    case 'approval':
      return (
        <ApprovalView
          approval={block}
          onApprove={() => onApprove?.(block.id)}
          onDeny={() => onDeny?.(block.id)}
        />
      );
    case 'config':
      return <ConfigView config={block} />;
    case 'error':
      return <ErrorView error={block} />;
    default:
      return null;
  }
}
```

### Diff View Component

```typescript
// components/blocks/DiffView.tsx

import React from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';

interface Props {
  diff: DiffBlock;
}

export function DiffView({ diff }: Props) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.filename}>{diff.filename}</Text>
        <Text style={styles.stats}>
          <Text style={styles.additions}>+{diff.stats.additions}</Text>
          {' '}
          <Text style={styles.deletions}>-{diff.stats.deletions}</Text>
        </Text>
      </View>
      <ScrollView horizontal style={styles.content}>
        <View>
          {diff.hunks.map((hunk, i) => (
            <View key={i}>
              <Text style={styles.hunkHeader}>{hunk.header}</Text>
              {hunk.lines.map((line, j) => (
                <Text
                  key={j}
                  style={[
                    styles.line,
                    line.type === 'addition' && styles.additionLine,
                    line.type === 'deletion' && styles.deletionLine,
                  ]}
                >
                  {line.type === 'addition' ? '+' : line.type === 'deletion' ? '-' : ' '}
                  {line.content}
                </Text>
              ))}
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#1e1e1e',
    borderRadius: 8,
    overflow: 'hidden',
    marginVertical: 8,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 12,
    backgroundColor: '#2d2d2d',
    borderBottomWidth: 1,
    borderBottomColor: '#3d3d3d',
  },
  filename: {
    color: '#e0e0e0',
    fontFamily: 'monospace',
    fontSize: 13,
  },
  stats: {
    fontSize: 12,
  },
  additions: {
    color: '#4caf50',
  },
  deletions: {
    color: '#f44336',
  },
  content: {
    padding: 12,
  },
  hunkHeader: {
    color: '#9e9e9e',
    fontFamily: 'monospace',
    fontSize: 12,
    marginBottom: 4,
  },
  line: {
    fontFamily: 'monospace',
    fontSize: 13,
    color: '#e0e0e0',
    lineHeight: 20,
  },
  additionLine: {
    backgroundColor: 'rgba(76, 175, 80, 0.2)',
    color: '#81c784',
  },
  deletionLine: {
    backgroundColor: 'rgba(244, 67, 54, 0.2)',
    color: '#e57373',
  },
});
```

### Plan View Component

```typescript
// components/blocks/PlanView.tsx

import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';

interface Props {
  plan: PlanBlock;
  onExecute?: () => void;
  onCancel?: () => void;
}

export function PlanView({ plan, onExecute, onCancel }: Props) {
  const getStepIcon = (status: PlanStep['status']) => {
    switch (status) {
      case 'pending': return '○';
      case 'running': return '◐';
      case 'completed': return '●';
      case 'failed': return '✕';
      case 'skipped': return '◌';
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>{plan.title}</Text>
      </View>

      <View style={styles.steps}>
        {plan.steps.map((step) => (
          <View key={step.index} style={styles.step}>
            <Text style={[
              styles.stepIcon,
              step.status === 'completed' && styles.completedIcon,
              step.status === 'failed' && styles.failedIcon,
            ]}>
              {getStepIcon(step.status)}
            </Text>
            <View style={styles.stepContent}>
              <Text style={styles.stepDescription}>{step.description}</Text>
              {step.command && (
                <Text style={styles.stepCommand}>{step.command}</Text>
              )}
            </View>
          </View>
        ))}
      </View>

      {plan.risks.length > 0 && (
        <View style={styles.risks}>
          <Text style={styles.risksTitle}>Risks:</Text>
          {plan.risks.map((risk, i) => (
            <Text key={i} style={styles.risk}>• {risk}</Text>
          ))}
        </View>
      )}

      {plan.requiresElevation && (
        <View style={styles.elevation}>
          <Text style={styles.elevationText}>
            🔒 Requires elevation
          </Text>
        </View>
      )}

      <View style={styles.actions}>
        <TouchableOpacity style={styles.cancelButton} onPress={onCancel}>
          <Text style={styles.cancelButtonText}>Cancel</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.executeButton} onPress={onExecute}>
          <Text style={styles.executeButtonText}>Execute</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#1a237e',
    borderRadius: 8,
    overflow: 'hidden',
    marginVertical: 8,
  },
  header: {
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#283593',
  },
  title: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '600',
  },
  steps: {
    padding: 12,
  },
  step: {
    flexDirection: 'row',
    marginBottom: 12,
  },
  stepIcon: {
    width: 24,
    fontSize: 16,
    color: '#9fa8da',
  },
  completedIcon: {
    color: '#4caf50',
  },
  failedIcon: {
    color: '#f44336',
  },
  stepContent: {
    flex: 1,
  },
  stepDescription: {
    color: '#e8eaf6',
    fontSize: 14,
  },
  stepCommand: {
    color: '#7986cb',
    fontFamily: 'monospace',
    fontSize: 12,
    marginTop: 4,
  },
  risks: {
    padding: 12,
    backgroundColor: 'rgba(255, 152, 0, 0.1)',
    borderTopWidth: 1,
    borderTopColor: '#283593',
  },
  risksTitle: {
    color: '#ffb74d',
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 4,
  },
  risk: {
    color: '#ffe0b2',
    fontSize: 13,
    marginLeft: 8,
  },
  elevation: {
    padding: 8,
    backgroundColor: 'rgba(244, 67, 54, 0.1)',
  },
  elevationText: {
    color: '#ef9a9a',
    fontSize: 13,
    textAlign: 'center',
  },
  actions: {
    flexDirection: 'row',
    padding: 12,
    gap: 12,
  },
  cancelButton: {
    flex: 1,
    padding: 12,
    borderRadius: 6,
    backgroundColor: '#37474f',
    alignItems: 'center',
  },
  cancelButtonText: {
    color: '#eceff1',
    fontWeight: '600',
  },
  executeButton: {
    flex: 1,
    padding: 12,
    borderRadius: 6,
    backgroundColor: '#3f51b5',
    alignItems: 'center',
  },
  executeButtonText: {
    color: '#ffffff',
    fontWeight: '600',
  },
});
```

### Approval View Component

```typescript
// components/blocks/ApprovalView.tsx

import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';

interface Props {
  approval: ApprovalBlock;
  onApprove: () => void;
  onDeny: () => void;
}

export function ApprovalView({ approval, onApprove, onDeny }: Props) {
  const isResolved = approval.status !== 'pending';

  return (
    <View style={[
      styles.container,
      approval.status === 'approved' && styles.approvedContainer,
      approval.status === 'denied' && styles.deniedContainer,
    ]}>
      <View style={styles.header}>
        <Text style={styles.icon}>
          {approval.status === 'pending' ? '⚠️' :
           approval.status === 'approved' ? '✓' : '✕'}
        </Text>
        <Text style={styles.title}>{approval.title}</Text>
      </View>

      <Text style={styles.description}>{approval.description}</Text>

      {!isResolved && (
        <View style={styles.actions}>
          <TouchableOpacity style={styles.denyButton} onPress={onDeny}>
            <Text style={styles.denyButtonText}>Deny</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.approveButton} onPress={onApprove}>
            <Text style={styles.approveButtonText}>Approve</Text>
          </TouchableOpacity>
        </View>
      )}

      {isResolved && (
        <Text style={styles.resolvedText}>
          {approval.status === 'approved' ? 'Approved' : 'Denied'}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#ff8f00',
    borderRadius: 8,
    padding: 12,
    marginVertical: 8,
  },
  approvedContainer: {
    backgroundColor: '#2e7d32',
  },
  deniedContainer: {
    backgroundColor: '#c62828',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  icon: {
    fontSize: 18,
    marginRight: 8,
  },
  title: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '600',
    flex: 1,
  },
  description: {
    color: 'rgba(255, 255, 255, 0.9)',
    fontSize: 14,
    marginBottom: 12,
  },
  actions: {
    flexDirection: 'row',
    gap: 12,
  },
  denyButton: {
    flex: 1,
    padding: 10,
    borderRadius: 6,
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
    alignItems: 'center',
  },
  denyButtonText: {
    color: '#ffffff',
    fontWeight: '600',
  },
  approveButton: {
    flex: 1,
    padding: 10,
    borderRadius: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.3)',
    alignItems: 'center',
  },
  approveButtonText: {
    color: '#ffffff',
    fontWeight: '600',
  },
  resolvedText: {
    color: 'rgba(255, 255, 255, 0.8)',
    fontSize: 13,
    fontStyle: 'italic',
    textAlign: 'center',
  },
});
```

---

## Native mTLS Module

React Native's built-in `fetch` doesn't support client certificates. We need a native module.

### Expo Config Plugin

```typescript
// plugins/withMTLS.ts

import { ConfigPlugin, withAndroidManifest, withInfoPlist } from 'expo/config-plugins';

const withMTLS: ConfigPlugin = (config) => {
  // iOS: Enable App Transport Security exceptions for self-signed certs
  config = withInfoPlist(config, (config) => {
    config.modResults.NSAppTransportSecurity = {
      NSAllowsArbitraryLoads: false,
      NSExceptionDomains: {
        // Allow local network for ELLE gateways
        'local': {
          NSExceptionAllowsInsecureHTTPLoads: false,
          NSIncludesSubdomains: true,
          NSExceptionRequiresForwardSecrecy: false,
          NSExceptionMinimumTLSVersion: 'TLSv1.2',
        },
      },
    };
    return config;
  });

  return config;
};

export default withMTLS;
```

### Native Module Specification

For a production implementation, create native modules:

**iOS (Swift):**
```swift
// ios/MTLSModule.swift

import Foundation

@objc(MTLSModule)
class MTLSModule: NSObject {

  @objc
  func fetch(
    _ url: String,
    options: NSDictionary,
    resolver: @escaping RCTPromiseResolveBlock,
    rejecter: @escaping RCTPromiseRejectBlock
  ) {
    // Load client certificate from PEM
    // Configure URLSession with client cert
    // Perform request with certificate pinning
  }
}
```

**Android (Kotlin):**
```kotlin
// android/app/src/main/java/com/ellemobile/MTLSModule.kt

class MTLSModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {

  @ReactMethod
  fun fetch(url: String, options: ReadableMap, promise: Promise) {
    // Load client certificate from PEM
    // Configure OkHttpClient with client cert
    // Perform request with certificate pinning
  }
}
```

### Alternative: Use Existing Library

Consider `react-native-ssl-pinning` or `rn-fetch-blob` with SSL support, though they may need modifications for full mTLS client cert support.

---

## Gateway API Extensions

The mobile gateway needs additional endpoints for configuration management:

### New Gateway Endpoints

```python
# Add to gateway.py

@app.get("/v1/config")
async def get_config(
    auth: MobileAuthContext = Depends(get_auth),
):
    """Get current ELLE configuration."""
    # Only operators can view full config
    if auth.effective_role != MobileRole.MOBILE_OPERATOR:
        raise HTTPException(
            status_code=403,
            detail="Configuration access requires operator role"
        )

    config = load_config()
    return {
        "daemon": {
            "telemetry_enabled": config.daemon.telemetry_enabled,
            "event_retention_days": config.daemon.event_retention_days,
            "manvault_auto_index": config.daemon.manvault_auto_index,
            "capability_bootstrap_enabled": config.daemon.capability_bootstrap_enabled,
            "auto_learn_new_packages": config.daemon.auto_learn_new_packages,
        },
        "llm": {
            "ollama_host": config.llm.ollama_host,
            "llm_model": config.llm.llm_model,
            "embedding_model": config.llm.embedding_model,
            "context_window": config.llm.context_window,
        },
        "mobile": {
            "enabled": config.mobile.enabled,
            "bind_host": config.mobile.bind_host,
            "bind_port": config.mobile.bind_port,
            "max_paired_devices": config.mobile.max_paired_devices,
            "default_role": config.mobile.default_role,
        },
    }


@app.put("/v1/config")
async def update_config(
    request: Request,
    auth: MobileAuthContext = Depends(get_auth),
):
    """Update ELLE configuration."""
    if auth.effective_role != MobileRole.MOBILE_OPERATOR:
        raise HTTPException(
            status_code=403,
            detail="Configuration changes require operator role"
        )

    body = await request.json()

    # Validate and apply configuration changes
    # This should trigger config reload on the daemon
    result = await deps.proxy.proxy_config_update(auth, body)

    deps.audit.log_request(
        device_id=auth.device_id,
        device_name=auth.device_name,
        endpoint="/v1/config",
        execution_mode="config_update",
        ip_address=auth.client_ip,
        success=True,
    )

    return {"status": "updated", "reload_required": result.get("reload_required", False)}
```

---

## Content Parsing

Parse ELLE responses for rich content blocks:

```typescript
// lib/content-parser.ts

interface ParsedContent {
  text: string;
  blocks: ContentBlock[];
}

/**
 * Parse ELLE response for embedded content blocks.
 *
 * ELLE uses markers like:
 * - ```diff ... ``` for diffs
 * - ```plan ... ``` for execution plans
 * - ```json {"type": "approval", ...} ``` for approvals
 * - Standard code blocks with language hints
 */
export function parseContent(content: string): ParsedContent {
  const blocks: ContentBlock[] = [];
  let text = content;

  // Parse diff blocks
  const diffRegex = /```diff\n([\s\S]*?)```/g;
  let match;
  while ((match = diffRegex.exec(content)) !== null) {
    const diffBlock = parseDiff(match[1]);
    if (diffBlock) {
      blocks.push(diffBlock);
      text = text.replace(match[0], `[DIFF:${blocks.length - 1}]`);
    }
  }

  // Parse plan blocks
  const planRegex = /```plan\n([\s\S]*?)```/g;
  while ((match = planRegex.exec(content)) !== null) {
    const planBlock = parsePlan(match[1]);
    if (planBlock) {
      blocks.push(planBlock);
      text = text.replace(match[0], `[PLAN:${blocks.length - 1}]`);
    }
  }

  // Parse JSON blocks for structured content
  const jsonRegex = /```json\n(\{[\s\S]*?\})\n```/g;
  while ((match = jsonRegex.exec(content)) !== null) {
    try {
      const parsed = JSON.parse(match[1]);
      if (parsed.type && ['approval', 'config', 'error'].includes(parsed.type)) {
        blocks.push(parsed as ContentBlock);
        text = text.replace(match[0], `[${parsed.type.toUpperCase()}:${blocks.length - 1}]`);
      }
    } catch {
      // Not valid JSON, skip
    }
  }

  // Parse standard code blocks
  const codeRegex = /```(\w+)?\n([\s\S]*?)```/g;
  while ((match = codeRegex.exec(content)) !== null) {
    blocks.push({
      type: 'code',
      language: match[1] || 'text',
      content: match[2],
    });
    text = text.replace(match[0], `[CODE:${blocks.length - 1}]`);
  }

  return { text, blocks };
}

function parseDiff(content: string): DiffBlock | null {
  const lines = content.split('\n');
  const hunks: DiffHunk[] = [];
  let currentHunk: DiffHunk | null = null;
  let filename = 'file';
  let additions = 0;
  let deletions = 0;

  for (const line of lines) {
    if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      filename = line.slice(4).trim();
    } else if (line.startsWith('@@')) {
      if (currentHunk) hunks.push(currentHunk);
      currentHunk = { header: line, lines: [] };
    } else if (currentHunk) {
      if (line.startsWith('+') && !line.startsWith('+++')) {
        currentHunk.lines.push({ type: 'addition', content: line.slice(1) });
        additions++;
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        currentHunk.lines.push({ type: 'deletion', content: line.slice(1) });
        deletions++;
      } else {
        currentHunk.lines.push({ type: 'context', content: line.startsWith(' ') ? line.slice(1) : line });
      }
    }
  }

  if (currentHunk) hunks.push(currentHunk);

  if (hunks.length === 0) return null;

  return {
    type: 'diff',
    filename,
    hunks,
    stats: { additions, deletions },
  };
}

function parsePlan(content: string): PlanBlock | null {
  try {
    // Try JSON format first
    const parsed = JSON.parse(content);
    return {
      type: 'plan',
      title: parsed.title || 'Execution Plan',
      steps: parsed.steps.map((s: any, i: number) => ({
        index: i + 1,
        description: s.description || s,
        command: s.command,
        status: 'pending',
      })),
      risks: parsed.risks || [],
      requiresElevation: parsed.requires_elevation || false,
    };
  } catch {
    // Parse text format
    const lines = content.split('\n').filter(l => l.trim());
    const steps: PlanStep[] = [];
    const risks: string[] = [];
    let title = 'Execution Plan';
    let inRisks = false;

    for (const line of lines) {
      if (line.startsWith('# ')) {
        title = line.slice(2);
      } else if (line.toLowerCase().includes('risk')) {
        inRisks = true;
      } else if (inRisks && line.startsWith('- ')) {
        risks.push(line.slice(2));
      } else if (/^\d+\./.test(line)) {
        steps.push({
          index: steps.length + 1,
          description: line.replace(/^\d+\.\s*/, ''),
          status: 'pending',
        });
      }
    }

    if (steps.length === 0) return null;

    return {
      type: 'plan',
      title,
      steps,
      risks,
      requiresElevation: content.toLowerCase().includes('elevation') ||
                         content.toLowerCase().includes('sudo'),
    };
  }
}
```

---

## Theming

### Color Palette

```typescript
// constants/colors.ts

export const colors = {
  // Base
  background: '#121212',
  surface: '#1e1e1e',
  surfaceVariant: '#2d2d2d',

  // Text
  textPrimary: '#ffffff',
  textSecondary: '#b0b0b0',
  textMuted: '#757575',

  // Accent
  primary: '#6366f1',      // Indigo
  primaryVariant: '#4f46e5',
  secondary: '#22c55e',    // Green

  // Status
  success: '#4caf50',
  warning: '#ff9800',
  error: '#f44336',
  info: '#2196f3',

  // Connection status
  connected: '#4caf50',
  disconnected: '#9e9e9e',
  elevated: '#ff9800',

  // Diff colors
  diffAddition: '#81c784',
  diffAdditionBg: 'rgba(76, 175, 80, 0.2)',
  diffDeletion: '#e57373',
  diffDeletionBg: 'rgba(244, 67, 54, 0.2)',

  // Code
  codeBg: '#1a1a1a',
  codeText: '#e0e0e0',
};
```

### Typography

```typescript
// constants/typography.ts

export const typography = {
  // Headings
  h1: {
    fontSize: 24,
    fontWeight: '700' as const,
    lineHeight: 32,
  },
  h2: {
    fontSize: 20,
    fontWeight: '600' as const,
    lineHeight: 28,
  },
  h3: {
    fontSize: 16,
    fontWeight: '600' as const,
    lineHeight: 24,
  },

  // Body
  body: {
    fontSize: 15,
    fontWeight: '400' as const,
    lineHeight: 22,
  },
  bodySmall: {
    fontSize: 13,
    fontWeight: '400' as const,
    lineHeight: 18,
  },

  // Code
  code: {
    fontFamily: 'JetBrainsMono' as const,
    fontSize: 13,
    lineHeight: 20,
  },

  // Labels
  label: {
    fontSize: 12,
    fontWeight: '500' as const,
    letterSpacing: 0.5,
  },
};
```

---

## Testing Strategy

### Unit Tests

```typescript
// __tests__/content-parser.test.ts

describe('parseContent', () => {
  it('parses diff blocks', () => {
    const content = `Here's the change:\n\`\`\`diff\n--- a/test.txt\n+++ b/test.txt\n@@ -1,3 +1,3 @@\n line1\n-old\n+new\n line3\n\`\`\``;
    const result = parseContent(content);
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0].type).toBe('diff');
  });

  it('parses plan blocks', () => {
    const content = `\`\`\`plan\n{"title":"Test","steps":[{"description":"Step 1"}]}\n\`\`\``;
    const result = parseContent(content);
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0].type).toBe('plan');
  });
});
```

### Integration Tests

```typescript
// __tests__/gateway-client.test.ts

describe('GatewayClient', () => {
  it('handles streaming responses', async () => {
    const client = new GatewayClient(mockConfig);
    const chunks: string[] = [];

    await client.chat(
      [{ role: 'user', content: 'Hello' }],
      { stream: true, onChunk: (c) => chunks.push(c) }
    );

    expect(chunks.length).toBeGreaterThan(0);
  });
});
```

### E2E Tests

```typescript
// e2e/pairing.test.ts

describe('Device Pairing', () => {
  it('completes pairing flow', async () => {
    // Start mock gateway
    // Scan mock QR code
    // Verify credentials stored
    // Verify machine appears in list
  });
});
```

---

## Build & Distribution

### App Configuration

```json
// app.json
{
  "expo": {
    "name": "ELLE",
    "slug": "elle-mobile",
    "version": "1.0.0",
    "orientation": "portrait",
    "icon": "./assets/icon.png",
    "scheme": "elle",
    "userInterfaceStyle": "dark",
    "splash": {
      "image": "./assets/splash.png",
      "resizeMode": "contain",
      "backgroundColor": "#121212"
    },
    "ios": {
      "bundleIdentifier": "com.elle.mobile",
      "supportsTablet": false,
      "infoPlist": {
        "NSCameraUsageDescription": "ELLE needs camera access to scan QR codes for pairing with your machines."
      }
    },
    "android": {
      "package": "com.elle.mobile",
      "adaptiveIcon": {
        "foregroundImage": "./assets/adaptive-icon.png",
        "backgroundColor": "#121212"
      },
      "permissions": ["CAMERA"]
    },
    "plugins": [
      "expo-camera",
      "expo-secure-store",
      "./plugins/withMTLS"
    ]
  }
}
```

### Build Commands

```bash
# Development
npx expo start

# Build for testing
eas build --profile preview --platform all

# Production build
eas build --profile production --platform all

# Submit to stores
eas submit --platform ios
eas submit --platform android
```

---

## Security Considerations

### Certificate Storage

- All certificates stored in platform secure storage (Keychain/Keystore)
- Certificates never logged or sent to analytics
- Memory wiped after use where possible

### Certificate Pinning

- Always pin to expected server fingerprint from QR code
- Reject connections if fingerprint doesn't match
- No fallback to system trust store

### Data at Rest

- Conversation history encrypted with device key
- Credentials never stored in plain AsyncStorage
- Clear data on machine removal

### Network Security

- mTLS for all gateway communication
- No HTTP fallback
- Minimum TLS 1.2

### App Security

- No analytics or telemetry by default
- No external network calls except to configured gateways
- Biometric/PIN unlock option for app access

---

## Future Enhancements

### Phase 2

- [ ] Push notifications for pending approvals
- [ ] Widget for quick status check
- [ ] Siri/Google Assistant shortcuts
- [ ] Apple Watch companion app

### Phase 3

- [ ] Offline mode with queued commands
- [ ] Multi-user support per machine
- [ ] File browser integration
- [ ] Terminal emulator mode

---

## Project Structure

```
elle-mobile/
├── app/                      # Expo Router screens
│   ├── _layout.tsx
│   ├── index.tsx
│   ├── scan.tsx
│   ├── onboarding.tsx
│   ├── (tabs)/
│   │   ├── _layout.tsx
│   │   ├── machines.tsx
│   │   ├── chat.tsx
│   │   └── settings.tsx
│   └── machine/
│       └── [id]/
│           ├── index.tsx
│           ├── config.tsx
│           └── history.tsx
├── components/
│   ├── blocks/
│   │   ├── DiffView.tsx
│   │   ├── PlanView.tsx
│   │   ├── CodeView.tsx
│   │   ├── ApprovalView.tsx
│   │   ├── ConfigView.tsx
│   │   └── ErrorView.tsx
│   ├── chat/
│   │   ├── ChatInput.tsx
│   │   ├── ChatMessage.tsx
│   │   └── ChatList.tsx
│   ├── common/
│   │   ├── Button.tsx
│   │   ├── Card.tsx
│   │   └── Input.tsx
│   └── ContentBlockRenderer.tsx
├── lib/
│   ├── gateway-client.ts
│   ├── pairing-service.ts
│   ├── credential-store.ts
│   ├── content-parser.ts
│   └── utils.ts
├── store/
│   └── index.ts
├── constants/
│   ├── colors.ts
│   └── typography.ts
├── hooks/
│   ├── useGateway.ts
│   ├── useMachine.ts
│   └── useChat.ts
├── plugins/
│   └── withMTLS.ts
├── __tests__/
├── app.json
├── package.json
└── tsconfig.json
```

---

## Getting Started

```bash
# Clone repository
git clone https://github.com/elle-project/elle-mobile.git
cd elle-mobile

# Install dependencies
npm install

# Start development server
npx expo start

# Run on iOS simulator
npx expo run:ios

# Run on Android emulator
npx expo run:android
```

### Development Prerequisites

- Node.js 18+
- Expo CLI (`npm install -g expo-cli`)
- For iOS: Xcode 15+ with iOS 17 SDK
- For Android: Android Studio with SDK 34+
- EAS CLI for builds (`npm install -g eas-cli`)
