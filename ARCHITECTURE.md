# Orchestratore AME Digital — Note architetturali

## Contesto

Sistema multiagente per la correzione e gestione di flussi n8n, uso interno al team AME Digital.
Un'unica istanza n8n condivisa (n8n.amedigital.???), ogni membro del team ha il proprio account.

---

## Principio guida: identità = API key personale

Ogni utente opera con la propria API key n8n. Il sistema non sa dell'esistenza degli altri profili.
Questo garantisce isolamento naturale: Claude non può toccare workflow di colleghi a meno che
l'utente autenticato non abbia i permessi n8n per farlo.

**Switch di profilo**: se un utente è titolato a operare sul profilo di un collega,
usa le proprie credenziali (che n8n già autorizza) — non servono credenziali altrui.

---

## Componenti

```
┌─────────────────────────────────────────────────────────┐
│                     Chatbot Interface                    │
│              (TBD: web app / Telegram / Slack)           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  Orchestrator Agent                      │
│         (Claude — routing, coordinamento, UX)            │
└──────┬──────────────────┬──────────────────┬────────────┘
       │                  │                  │
┌──────▼──────┐  ┌────────▼──────┐  ┌───────▼───────┐
│  Analyzer   │  │    Fixer      │  │   Validator   │
│             │  │               │  │               │
│ legge flow  │  │ propone patch │  │ verifica fix  │
│ + exec logs │  │ al JSON flow  │  │ prima di push │
└──────┬──────┘  └────────┬──────┘  └───────┬───────┘
       └──────────────────┴──────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│                    n8n Tool Layer                         │
│  get_workflow / get_executions / update_workflow /        │
│  activate_workflow / list_workflows                       │
└─────────────────────────┬────────────────────────────────┘
                          │  (HTTPS + Bearer API key)
                ┌─────────▼──────────┐
                │  n8n.amedigital.?  │
                └────────────────────┘
```

---

## Agenti — responsabilità (struttura confermata: 4 agenti)

| Agente | Input | Output |
|--------|-------|--------|
| **Orchestrator** | messaggio utente in linguaggio naturale | coordina gli altri, gestisce dialogo Telegram e checkpoint HITL |
| **Analyzer** | workflow JSON + execution logs | diagnosi strutturata — solo analisi, nessuna proposta di fix |
| **Fixer** | diagnosi dell'Analyzer | JSON modificato + spiegazione human-readable delle modifiche |
| **Validator** | JSON originale + patch del Fixer | semaforo verde oppure lista obiezioni |

Note implementative:
- L'utente vede solo l'output dell'Orchestrator
- Analyzer e Validator girano sullo stesso modello con **system prompt diversi** — il Validator non sa di essere "lo stesso" del Fixer (evita confirmation bias)
- Nessun agente agisce su n8n direttamente: solo l'Orchestrator chiama i tool, su conferma utente

---

## Tool n8n (API REST)

| Tool | Metodo | Endpoint |
|------|--------|----------|
| `list_workflows` | GET | `/api/v1/workflows` |
| `get_workflow` | GET | `/api/v1/workflows/{id}` |
| `get_executions` | GET | `/api/v1/executions?workflowId={id}` |
| `update_workflow` | PUT | `/api/v1/workflows/{id}` |
| `activate_workflow` | POST | `/api/v1/workflows/{id}/activate` |
| `deactivate_workflow` | POST | `/api/v1/workflows/{id}/deactivate` |

Tutti autenticati con `X-N8N-API-KEY: {user_api_key}`.

---

## Sicurezza credenziali — requisiti

Questo è il punto critico. Le API key n8n danno accesso completo ai workflow dell'utente.

### At rest
- Le key **non vengono mai salvate in chiaro**
- Encrypted con AES-256 prima di essere scritte su DB
- La chiave di cifratura (master key) non è nel DB — separata (env var o secret manager)

### In transit
- Solo HTTPS tra chatbot ↔ backend e backend ↔ n8n
- Le key non passano mai nel body di log o response API

### In memory
- La key viene decifrata solo a inizio sessione, tenuta in memoria per la durata della sessione
- Non finisce mai in log, tracce o messaggi di errore

### Audit
- Ogni operazione su n8n viene loggata: chi, cosa, quando, su quale workflow
- I log non contengono la key, solo l'identità utente e l'azione

### Rotazione
- L'utente può aggiornare la propria key senza perdere la storia
- La vecchia key viene sovrascritta (non mantenuta)

---

## Rollback

### Strategia: snapshot automatico pre-modifica

Prima di ogni `update_workflow`, il sistema salva automaticamente il JSON completo del workflow
su Supabase. Non serve chiedere — è trasparente e sempre attivo.

```
update_workflow(id, new_json)
  └─ 1. GET /workflows/{id}          ← leggi stato attuale
  └─ 2. salva snapshot su Supabase   ← SEMPRE, prima di tutto
  └─ 3. PUT /workflows/{id}          ← applica modifica
```

### Schema Supabase: tabella `workflow_snapshots`

| Colonna | Tipo | Descrizione |
|---------|------|-------------|
| `id` | uuid | PK |
| `batch_id` | uuid | raggruppa i flow modificati nella stessa operazione |
| `user_id` | text | chi ha fatto la modifica |
| `workflow_id` | text | ID n8n del workflow |
| `workflow_name` | text | nome leggibile (per il bot) |
| `snapshot_json` | jsonb | JSON completo del workflow prima della modifica |
| `was_active` | bool | stato attivazione prima della modifica |
| `operation` | text | descrizione dell'operazione (es. "aggiorna modello Claude") |
| `created_at` | timestamp | quando è stato salvato lo snapshot |

### Rollback singolo flow

```
Utente: "Annulla la modifica a sync-products"

1. Bot mostra: "sync-products modificato il 18/05 alle 14:32 (aggiorna modello Claude)"
2. "Ripristino il workflow alla versione precedente. Confermi?" [Sì / No]
3. PUT /workflows/{id} con snapshot_json
4. Se was_active=true → riattiva; se false → lascia disattivo
5. Snapshot usato marcato come rolled_back (non eliminato — serve storia)
```

### Rollback di un intero batch

```
Utente: "Annulla tutto quello che hai fatto prima"

1. Bot mostra lista flow del batch con timestamp
2. "Ripristino 7 workflow. Questa operazione non si può annullare. Confermi?" [Sì / No]
3. Rollback uno per uno, con progress live
4. Stop disponibile anche qui — mostra cosa è già stato ripristinato
```

### Cosa il rollback NON può fare (da comunicare chiaramente all'utente)

- **Non annulla le esecuzioni già avvenute** con il workflow modificato
- **Non ripristina workflow eliminati** (operazione di delete bloccata by design — non esposta come tool)
- **Non è infinito**: si conservano gli ultimi N snapshot per workflow (es. 10), i più vecchi vengono purgati

### Retention snapshot

- Default: ultimi **10 snapshot per workflow**
- I batch recenti sono sempre conservati integralmente (anche se superano quota)
- Purge automatico dei più vecchi via cron o trigger Supabase

---

## Human-in-the-Loop (HITL)

Principio: **più flow coinvolti = più checkpoint obbligatori**. Il sistema non applica mai
modifiche in autonomia. L'utente è sempre l'ultimo a premere "vai".

### Livelli di rischio e checkpoint

| Livello | Scenario | Checkpoint |
|---------|----------|------------|
| **L1** | Modifica su 1 flow, cambio minore (es. rename nodo) | 1 conferma: "Applico?" |
| **L2** | Modifica su 1 flow, cambio strutturale (es. logica nodo) | Mostra diff → conferma |
| **L3** | Modifica su N flow, stessa operazione (es. aggiorna modello Claude) | Mostra lista flow coinvolti + diff campione → conferma batch → progress live |
| **L4** | Modifica su N flow, operazioni diverse | Revisione obbligatoria flow per flow, nessun "applica tutto" |

### Caso d'uso emblematico: aggiornamento modello Claude su tutti i nodi AI

```
Utente: "Aggiorna il modello Claude a claude-opus-4-7 in tutti i flussi"

1. Analyzer scansiona tutti i workflow
2. Trova: 12 nodi AI distribuiti su 7 workflow
3. Bot mostra riepilogo:
   ──────────────────────────────────────
   Ho trovato 12 nodi AI su 7 workflow:
   • sync-products        → 3 nodi  (claude-3-opus → claude-opus-4-7)
   • daily-report         → 2 nodi  (claude-3-sonnet → claude-opus-4-7)
   • notify-slack         → 1 nodo  (claude-3-opus → claude-opus-4-7)
   [... altri 4 workflow ...]

   Vuoi procedere? [Sì / No / Mostrami i dettagli]
   ──────────────────────────────────────
4. Utente sceglie "Mostrami i dettagli" → diff per ogni workflow
5. Utente conferma → il sistema aggiorna UN flow alla volta
6. Dopo ogni flow: "✓ sync-products aggiornato (3/7). Continuo?"
   (opzione pausa/annulla sempre disponibile)
7. Al termine: riepilogo finale + audit log
```

### Regole fisse (non bypassabili)

- **Mai agire su più di 1 flow senza conferma esplicita**
- **Mai attivare un flow modificato senza conferma separata** (modifica e attivazione sono due step distinti)
- **Operazioni batch sempre interrompibili** — se l'utente manda "stop" a metà, il sistema si ferma e mostra cosa è già stato modificato
- **Dry-run disponibile sempre** — l'utente può chiedere "cosa faresti?" senza che nulla venga applicato

---

## Flusso utente tipo

```
1. Utente apre chatbot → si identifica (es. login chatbot)
2. Se prima volta: incolla la sua n8n API key → salvata cifrata
3. "Il flusso sync-products si rompe ogni notte"
4. Orchestrator → Analyzer legge il flusso + ultimi execution errors
5. Analyzer → Fixer propone correzione con diff leggibile
6. Fixer → Validator controlla la patch
7. Bot mostra all'utente: diff + spiegazione. "Applico?" [Sì / No / Dettagli]
8. Utente conferma modifica → update_workflow
9. Bot chiede separatamente: "Riattivo il flow?" [Sì / No]
10. Audit log aggiornato
```

---

## Decisioni prese

| Decisione | Scelta | Note |
|-----------|--------|-------|
| Interfaccia chatbot | **Telegram bot** | Il più comodo da prototipare e usare su mobile; `python-telegram-bot` si integra facilmente con il loop agente |
| Store credenziali | **Supabase aziendale** | Già disponibile, tabella `user_credentials` con key cifrate; master key in env var |
| URL istanza n8n | `https://n8n.amedigital.it` | API base: `https://n8n.amedigital.it/api/v1/` |

---

## Decisioni aperte (da risolvere nella prossima sessione)

- [ ] Entitlement cross-profilo: gestito solo da n8n (ruoli nativi) o serve una lista separata?
- [ ] Modello agenti: un file per agente o classe con ruolo iniettato?
- [ ] Deploy: server sempre acceso (necessario per il bot Telegram) — VPS, Railway, Render, o altro?
- [ ] Telegram: bot privato (solo whitelist chat_id del team) o con auth propria?

---

## Stack tecnico

### Attuale
- `agent/orchestrator.py` — loop Claude + tool dispatch (base funzionante)
- `agent/requirements.txt` — `anthropic>=0.102.0`, `httpx>=0.28.1`
- `.github/workflows/agent-orchestrator.yml` — trigger manuale + cron

### Da aggiungere
- `agent/tools/n8n.py` — tool layer completo (list/get/update/activate workflow + executions)
- `agent/tools/credentials.py` — cifratura/decifratura key con Supabase
- `agent/agents/` — Analyzer, Fixer, Validator come moduli separati
- `bot/telegram_bot.py` — entry point Telegram, gestione sessioni utente
- `bot/requirements.txt` — aggiungere `python-telegram-bot`
