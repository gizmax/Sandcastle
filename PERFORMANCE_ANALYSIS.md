# Sandshore Performance Analysis - Skills vs. Current Implementation

## Benchmark: Sandstorm (old) vs Sandshore (current)

### Co se deje pri kazdem kroku workflow

**Sandstorm (proxy mode):**
```
Sandcastle -> HTTP POST /query -> Sandstorm server -> E2B API -> sandbox create
                                                   -> npm install SDK
                                                   -> run agent
                                                   <- SSE stream <- HTTP <- parse events
                                                   -> sandbox kill
```
- 2 network hops (Sandcastle->Sandstorm, Sandstorm->E2B)
- Sandstorm musi bezet jako separatni proces
- Latence: ~2-5s overhead na HTTP proxy layer

**Sandshore (direct E2B mode):**
```
Sandcastle -> E2B Python SDK -> sandbox create (~3-5s)
                             -> upload runner.mjs (~1s)
                             -> npm install SDK (~30-60s) <<<< BOTTLENECK
                             -> run agent (variable)
                             -> stream stdout via asyncio.Queue
                             -> sandbox kill
```
- 1 network hop (primo E2B API)
- Zadny externi proces
- Latence: ~1-2s overhead (E2B SDK je async nativne)

### Vyhra uz dnes
| Metrika | Sandstorm | Sandshore | Rozdil |
|---------|-----------|-----------|--------|
| Network hops | 2 | 1 | -50% |
| Externi dependency | ano (subprocess) | ne | eliminovano |
| Startup overhead | ~2-5s HTTP | ~1-2s SDK | -60% |
| Memory | 2 procesy | 1 proces | -50% |

### Zbyva opravit
| Bottleneck | Cas | Reseni |
|------------|-----|--------|
| npm install v kazdem sandboxu | 30-60s | Custom E2B template |
| Zadny concurrent limit | risk E2B rate limit | Semaphore |
| Queue polling 2s interval | max 2s zpozdeni | Lepsi drain pattern |
| Sandbox create per step | 3-5s per step | Sandbox pool |

---

## Analyza 8 skills - co nam realne pomuze

### 1. async-python-patterns - IMPLEMENTOVAT

**Co skill uci:**
- `asyncio.Semaphore` pro rate limiting
- `asyncio.Queue(maxsize=N)` pro backpressure
- Producer-consumer pattern
- Cancellation handling (`CancelledError`)
- `asyncio.wait_for()` pro timeouty

**Co nam chybi v sandshore.py:**

a) **Semaphore na sandbox creation** (radky 163-246)
- Ted: neomezeny pocet sandboxu - parallel_over s 20 items vytvori 20 sandboxu naraz
- E2B ma rate limity, tohle muze failnout
- Fix: `asyncio.Semaphore(max_concurrent_sandboxes)` v SandshoreRuntime

b) **Queue s maxsize** (radek 167)
- Ted: `asyncio.Queue()` bez limitu - pokud agent chrleny events, pamet roste
- Fix: `asyncio.Queue(maxsize=1000)` pro backpressure

c) **Cancellation handling** (radky 217-227)
- Ted: pokud se run cancelluje, sandbox bezi dal (a stoji penize)
- Fix: propagovat cancel signal do sandbox.kill()

d) **Cas ziskany:** Bez semaphore muzeme narazit E2B rate limity a failnout cele workflow. S limitem max 5 soucasnych sandboxu: stabilni + predvidatelny.
- **Impact:** VYSOKA stabilita, STREDNI rychlost (prevence E2B 429 erroru)
- **Effort:** ~20 radku kodu

### 2. fastapi-pro - CASTECNE

**Co skill uci:**
- `Depends()` pro injektovani service/client
- SSE streaming patterns
- Lifespan events pro pool init/cleanup
- Background tasks

**Co nam chybi:**

a) **Sandbox jako Depends()** (routes.py radek 64)
- Ted: `SandshoreRuntime` importovany primo, vytvaren inline v executoru
- Skill doporucuje: `Depends(get_runtime)` v route handlerech
- **Verdikt:** NEDELAT - nase architektura je jina, executor si runtime vytvari sam, dependency injection by pridala komplexitu bez vyhody

b) **SSE streaming improvements**
- Ted: SSE endpoint v routes.py uz existuje a funguje
- Skill neprinasi nic noveho nad to co mame
- **Verdikt:** NEDELAT

c) **Lifespan pro sandbox pool warmup**
- Ted: singletony se vytvari lazy (pri prvnim pouziti)
- Moznost: v lifespan() pred-vytvorit sandbox pool
- **Verdikt:** MOZNA v budoucnu, az budeme mit sandbox pooling

**Impact:** NIZKY pro soucasny stav
**Effort:** N/A

### 3. fastapi-templates - NEDELAT

**Co skill uci:**
- Repository pattern, Service layer
- Project structure app/api/v1/services/repositories
- Test conftest s AsyncClient

**Verdikt:** Sandcastle uz MA svou architekturu (api/engine/models/queue). Prepis do repository patternu by byl velky refaktor bez realneho performance gainu. Nase testy uz pouzivaji AsyncClient s TestClient. Zadny performance benefit.

**Impact:** ZADNY na vykon
**Effort:** VELKY (refaktor cele architektury)

### 4. python-testing-patterns - CASTECNE

**Co skill uci:**
- `pytest.mark.integration` marker
- Parametrizovane testy
- Mocking E2B SDK
- Async test fixtures

**Co nam chybi:**

a) **Integration testy pro Sandshore**
- Ted: vsechny testy mockuji SandshoreRuntime, zadny nezkusi realny E2B call
- Fix: pridat `@pytest.mark.integration` testy co skutecne vytvorite sandbox
- **Verdikt:** UDELAT az budeme mit stabilni E2B setup, ne ted

b) **Parametrizovane testy pro sandbox konfigurace**
- Ted: testy testuji jednu konfiguraci (proxy mode mock)
- Moznost: parametrizovat direct vs proxy vs no-config
- **Verdikt:** NICE TO HAVE, ne priorita

**Impact:** NIZKY na vykon, STREDNI na kvalitu
**Effort:** STREDNI

### 5. python-pro - NEDELAT

**Co skill uci:**
- `Protocol` typing pro interfaces
- Structural pattern matching
- Profiling s py-spy
- `functools.lru_cache`

**Verdikt:** Hezke patterny, ale zadny realny performance gain. Protocol typing by zprehlednila sandbox interface pro mocking, ale nase testy uz fungujou. Pattern matching je stylovy ale neprinese rychlost. Profiling dava smysl az budeme resit bottlenecky, ktery nezname - ted presne vime co je pomale (npm install).

**Impact:** ZADNY
**Effort:** MALY ale zbytecny

### 6. docker-expert - IMPLEMENTOVAT (priorita 1)

**Co skill uci:**
- Multi-stage builds pro minimalni images
- Predinstalace dependencies
- Health checks

**Jak to aplikovat na E2B:**

**TOTO JE NEJVETSI WIN.** E2B custom template = Docker image kde je uz predinstalovany Claude Agent SDK a runner.mjs.

Ted (kazdy step):
```
sandbox create     3-5s
upload runner.mjs  ~1s
npm install SDK    30-60s  <<<< TOHLE ZMIZI
run agent          variable
sandbox kill       ~1s
--------------------------
Overhead:          35-67s per step
```

S custom templatem:
```
sandbox create     3-5s  (SDK + runner uz v image)
run agent          variable
sandbox kill       ~1s
--------------------------
Overhead:          4-6s per step
```

**Uspora: 31-61 sekund na KAZDY step.**

Pro 5-step workflow: 2.5-5 minut usetreno.
Pro 10-step workflow: 5-10 minut usetreno.

**Jak na to:**
1. Vytvorit `e2b.Dockerfile` s predinstalovanym `@anthropic-ai/claude-agent-sdk`
2. Bake-in `runner.mjs` do image
3. Publishnout custom E2B template pres `e2b template build`
4. V sandshore.py pouzit `template="sandcastle-runner"` misto default template
5. Skip upload + npm install kdyz je custom template detekovan

**Impact:** EXTREMNE VYSOKY (~90% redukce overhead per step)
**Effort:** STREDNI (E2B template build + config)

### 7. api-patterns - CASTECNE

**Co skill uci:**
- Rate limiting (token bucket, sliding window)
- Response envelope
- Versioning
- Pagination

**Co nam chybi:**

a) **Rate limiting na /api/workflows/run**
- Ted: zadny limit - klient muze spamovat execution endpointy
- Kazdy call = novy E2B sandbox ($)
- Fix: jednoduchy in-memory rate limiter (max N runs/min per tenant)
- **Verdikt:** UDELAT - chroni pred nahodnym DDoS i pred billing exploity

b) **Response envelope, versioning, pagination**
- Ted: uz mame pagination meta, konzistentni response format
- **Verdikt:** HOTOVO

**Impact:** STREDNI (ochrana pred zbytecnymi naklady)
**Effort:** MALY (~30 radku middleware)

### 8. database-design - MOZNA POZDEJI

**Co skill uci:**
- Indexing strategy
- N+1 prevence
- Query optimalizace

**Co nam chybi:**

a) **Step result cache**
- Kdyz se stejny step se stejnym inputem spusti znova, vysledek by se mohl cachovat
- To by eliminovalo E2B sandbox call uplne pro opakovane kroky
- **Verdikt:** Je v roadmape, ale je to komplexni feature (cache invalidation, hash inputu, TTL)
- NEBUDE rychly win

b) **Indexy na run tabulkach**
- Mozna chybi indexy na `Run.status`, `Run.tenant_id`, `RunStep.run_id`
- **Verdikt:** Overit, ale tohle ovlivnuje API response time, ne execution speed

**Impact:** NIZKY na execution, STREDNI na API latency
**Effort:** MALY (indexy), VELKY (cache)

---

## Prioritizovany akcni plan

### UDELAT TED (session priority)

| # | Akce | Uspora | Effort | Skill |
|---|------|--------|--------|-------|
| 1 | **Custom E2B template** s predinstalovanym SDK | 30-60s/step | Stredni | docker-expert |
| 2 | **Semaphore** na concurrent sandboxes | Stabilita + prevence 429 | Maly | async-python |
| 3 | **Queue backpressure** (maxsize) | Memory safety | Trivial | async-python |
| 4 | **Cancellation propagation** do sandbox | $ uspora (stop running sandbox) | Maly | async-python |

### UDELAT POZDEJI

| # | Akce | Uspora | Effort | Skill |
|---|------|--------|--------|-------|
| 5 | Rate limiting middleware | Ochrana pred billing exploit | Maly | api-patterns |
| 6 | Step result cache | Eliminace opakovanych callu | Velky | database-design |
| 7 | Integration testy s E2B | Kvalita, ne rychlost | Stredni | testing-patterns |

### NEDELAT

| # | Akce | Duvod |
|---|------|-------|
| - | Service layer refaktor | Zadny perf gain, velky effort |
| - | Protocol typing | Kosmetika, ne vykon |
| - | Depends() injection | Nase architektura je jina |
| - | Pattern matching | Stylove ale zbytecne |

---

## Srovnani: pred vs. po optimalizaci

### 5-step sekvencni workflow

| Faze | Ted | Po optimalizaci | Uspora |
|------|-----|-----------------|--------|
| Sandbox create (x5) | 20s | 20s | - |
| Upload runner.mjs (x5) | 5s | 0s | 5s |
| npm install (x5) | 200s | 0s | **200s** |
| Agent execution (x5) | 300s | 300s | - |
| Sandbox kill (x5) | 5s | 5s | - |
| **Total** | **530s** | **325s** | **205s (-39%)** |

### 5-step paralelni workflow (s 3 parallel items each)

| Faze | Ted | Po optimalizaci | Uspora |
|------|-----|-----------------|--------|
| Sandbox create (x15) | Neomezene, mozny 429 | Max 5 soucasne, stabilni | stabilita |
| Upload + npm install (x15) | 65s (paralelne) | 0s | **65s** |
| Agent execution (x15) | 300s | 300s | - |
| **Total** | **365s + risk failu** | **300s + stabilni** | **65s + spolehlivost** |

---

## Zaver

Z 8 analyzovanych skills jsou **3 primo aplikovatelne** pro performance:
1. **docker-expert** -> custom E2B template (NEJVETSI WIN: -30-60s/step)
2. **async-python-patterns** -> semaphore + backpressure + cancellation (STABILITA)
3. **api-patterns** -> rate limiting (OCHRANA)

Zbytek (fastapi-pro, fastapi-templates, python-pro, database-design, python-testing) jsou bud uz implementovane, nebo prinasi kvalitu kodu bez meritelneho performance impactu.

**Doporuceni:** Implementovat body 1-4 z akcniho planu. Custom E2B template sam o sobe uspoří vic nez vsechny ostatni optimalizace dohromady.
