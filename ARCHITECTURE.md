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

## Agenti — responsabilità

| Agente | Input | Output |
|--------|-------|--------|
| **Orchestrator** | messaggio utente in linguaggio naturale | coordina gli altri, risponde al chatbot |
| **Analyzer** | workflow ID o nome | analisi del flusso + lista errori da execution log |
| **Fixer** | analisi dell'Analyzer | patch JSON del workflow con spiegazione delle modifiche |
| **Validator** | patch proposta dal Fixer | approvazione o lista di problemi residui |

L'utente vede solo l'output dell'Orchestrator. Chiede conferma prima di applicare qualsiasi modifica.

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

## Flusso utente tipo

```
1. Utente apre chatbot → si identifica (es. login chatbot)
2. Se prima volta: incolla la sua n8n API key → salvata cifrata
3. "Il flusso sync-products si rompe ogni notte"
4. Orchestrator → Analyzer legge il flusso + ultimi execution errors
5. Analyzer → Fixer propone correzione
6. Fixer → Validator controlla la patch
7. Orchestrator presenta all'utente: "Ho trovato X, propongo Y. Applico?"
8. Utente conferma → update_workflow + activate_workflow
9. Audit log aggiornato
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
