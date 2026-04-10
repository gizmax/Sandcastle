# Sandcastle v0.30 "Agents Unleashed" — Social Posts

---

## LinkedIn (CZ — hlavní post)

Nepostavili jsme platformu pro agenty. Postavili jsme chybějící vrstvu mezi nimi všemi.

Každý teď má AI agenty. Anthropic spustil Managed Agents — cloudové kontejnery, kde Claude může spouštět kód, prohledávat web, číst soubory. Je to silné. OpenAI má něco podobného. Google taky.

Problém nejsou agenti. Problém je, že každý z nich je ostrov.

Nemůžete vzít výsledek Claude researche a poslat ho Mistralu na levné formátování. Nemůžete citlivou část spustit lokálně na Macu a těžkou v cloudu. Nemůžete to naplánovat, aby to běželo každé ráno v 7 a poslalo vám email. Nemůžete z kombinovaného výstupu vygenerovat PDF report.

Do teď.

Sandcastle v0.30 zachází s Managed Agents jako s dalším krokem ve vašem workflow. Ne jako s celým workflow. Jako s krokem. Správný nástroj na správnou práci.

Krok 1: Claude Managed Agent prohledává web, stahuje datasety, spouští Python analýzu v cloudovém kontejneru. Má bash, přístup k souborům, web search — kompletní vývojové prostředí.

Krok 2: Mistral Small shrnuje poznatky. Stojí desetinu ceny Claude. Stejná kvalita na shrnutí.

Krok 3: GLM-OCR přečte vaši skenovanou smlouvu lokálně přes Ollama. 94,6% přesnost. Nulové náklady. Žádná data neopouštějí váš počítač.

Krok 4: Další Managed Agent porovnává research se smlouvou. Spustí statistickou analýzu v Pythonu.

Krok 5: Sandcastle PDF engine převede všechno do profesionálního reportu s grafy, obsahem, titulní stránkou.

Jeden YAML soubor. Pět kroků. Tři různí AI provideři. Cloud i lokální. Naplánované na každý pátek. Report o spotřebě tokenů říká přesně, co který krok stál.

Dodáváme 15 předpřipravených šablon agentů. Researcher, coder, analyst, writer, reviewer, scraper, tester, devops, translator, designer, SQL expert, SEO specialist, legal analyst, financial analyst, project manager. Jeden řádek: agent_template: researcher.

Nechcete šablony? Popište co potřebujete lidskou řečí: "Datový analytik co dělá matplotlib grafy z CSV souborů." AI navrhne agenta za vás — vybere model, nástroje, packages, síť. Napíšete jednu větu.

Agent selže uprostřed běhu? fallback_template: coder to zkusí s jiným specialistou. Výstup je příliš velký? output_max_tokens ho ořeže. Kroky si mohou sdílet soubory. Agenti mohou vracet strukturovaný JSON.

Ale to podstatné je: scheduling.

ChatGPT udělá jednu věc jednou. Claude udělá jednu věc jednou. Perplexity vyhledá jednou. Žádný z nich to neudělá každé ráno v 7, každý pátek v 18:00, pokaždé když se objeví soubor ve složce. Žádný z nich vám nepošle výsledky emailem, na Slack, do Google Sheetu, jako PDF.

My ano.

A děláme to z Evropy. EU data residency vynucená. Mistral jako výchozí provider — vaše data zůstanou v EU. EU AI Act compliance zabudovaná. Audit trail s SHA-256. Privacy router s PII redakcí.

Žádný americký konkurent tohle nemá. Ne protože to neumí postavit. Protože nemusí. My musíme. A to je naše výhoda.

Anthropic vám dá silný motor. My vám dáme auto, silnici, navigaci, jízdní řád, report o spotřebě a pojištění, že vaše data zůstanou tam kde mají.

Dvacet jedna typů kroků. Sedm AI providerů. Patnáct šablon agentů. Čtyři OCR enginy. Vše skladatelné. Vše plánovatelné. Vše auditovatelné.

Postaveno v Praze. Open source.

pip install sandcastle-ai==0.30.0
https://sandcastle-ai.eu

---

## LinkedIn (EN — main post)

We didn't build an agent platform. We built the missing layer between all of them.

Everybody has AI agents now. Anthropic launched Managed Agents — cloud containers where Claude can run code, search the web, read files. It's powerful. OpenAI has something similar. So does Google.

The problem isn't the agents. The problem is that each one is an island.

You can't take Claude's research output and feed it to Mistral for cheap formatting. You can't run the sensitive part locally on your Mac and the heavy part in the cloud. You can't schedule it to run every morning at 7 and email you the results. You can't generate a PDF report from the combined output.

Until now.

Sandcastle v0.30 treats Managed Agents as just another step in your workflow. Not the whole workflow. A step. The right tool for the right job.

Step 1: Claude Managed Agent searches the web, downloads datasets, runs Python analysis in a cloud container. It has bash, file access, web search — a full development environment.

Step 2: Mistral Small summarizes the findings. Costs 1/10th of Claude. Same quality for a summary.

Step 3: GLM-OCR reads your scanned contract locally via Ollama. 94.6% accuracy. Zero cost. Zero data leaving your machine.

Step 4: Another Managed Agent compares the research with the contract. Runs statistical analysis in Python.

Step 5: Sandcastle's PDF engine turns everything into a professional report with charts, table of contents, cover page.

One YAML file. Five steps. Three different AI providers. Cloud and local. Scheduled to run every Friday. Token waste report tells you exactly what each step cost.

We ship 15 built-in agent templates. Researcher, coder, analyst, writer, reviewer, scraper, tester, devops, translator, designer, SQL expert, SEO specialist, legal analyst, financial analyst, project manager. One line to use any of them: agent_template: researcher.

Don't like templates? Describe what you need in plain language: "Data analyst who creates matplotlib charts from CSV files." AI designs the agent for you — picks the right model, tools, packages, network settings. You just write one sentence.

Agent fails mid-run? fallback_template: coder retries with a different specialist. Output too large for the next step? output_max_tokens trims it. Steps can share files. Agents can output structured JSON for the next step to parse.

But here's the thing that matters: scheduling.

ChatGPT can do one thing once. Claude can do one thing once. Perplexity can search once. None of them can do it every morning at 7, every Friday at 18:00, every time a file appears in a folder. None of them can email you the results, post to Slack, update a Google Sheet, generate a PDF.

We can.

And we do it from Europe. EU data residency enforced. Mistral as default provider — your data stays in the EU. EU AI Act compliance built in. Audit trail with SHA-256 hash chain. Privacy router with PII redaction.

No American competitor has this. Not because they can't build it. Because they don't have to. We do. And that's our advantage.

Anthropic gives you a powerful engine. We give you the car, the road, the GPS, the schedule, the fuel efficiency report, and the insurance that your data stays where it should.

Twenty-one step types. Seven AI providers. Fifteen agent templates. Four OCR engines. All composable. All schedulable. All auditable.

Built in Prague. Open source.

pip install sandcastle-ai==0.30.0
https://sandcastle-ai.eu

---

## Tweet thread (CZ, 5 tweetů)

1/5
Sandcastle v0.30: "Agents Unleashed"

Claude Managed Agent hledá v cloudu. Mistral formátuje za desetinu ceny. GLM-OCR čte vaše skeny lokálně s 94,6% přesností. PDF engine udělá report.

Všechno v jednom YAML. Všechno naplánované. Dnes venku.

---

2/5
15 šablon agentů, jeden řádek:

agent_template: researcher
agent_template: financial_analyst
agent_template: legal_analyst

Nebo popište co chcete:
describe: "Datový analytik co dělá grafy z CSV"

AI navrhne agenta. Vy napíšete jednu větu.

---

3/5
Co to znamená:

Claude teď může spouštět kód, hledat na webu, číst soubory — jako JEDEN KROK ve větším pipeline.

Další krok může použít Mistral (10x levnější). Nebo oMLX (lokálně, zdarma). Nebo GLM-OCR (94,6%, zdarma).

Nikdo jiný vám nenechá mixovat providery v jednom workflow.

---

4/5
EU úhel, o kterém nikdo nemluví:

Mistral běží v EU. Vaše data zůstanou v Evropě.
EU AI Act compliance je zabudovaná.
Audit trail s SHA-256 hash chain.

Žádný americký konkurent tohle nemá. My ano. Z Prahy.

---

5/5
21 typů kroků. 7 providerů. 15 šablon agentů. 4 OCR enginy.

Vše skladatelné. Vše plánovatelné. Vše auditovatelné.

pip install sandcastle-ai==0.30.0
https://sandcastle-ai.eu/whatsnew/

---

## Tweet thread (EN, 5 tweets)

1/5
Sandcastle v0.30: "Agents Unleashed"

Claude Managed Agent researches in the cloud. Mistral formats for 1/10th cost. GLM-OCR reads your scans locally at 94.6%. PDF engine makes the report.

All in one YAML. All scheduled. Shipping today.

---

2/5
15 agent templates, one line each:

agent_template: researcher
agent_template: financial_analyst
agent_template: legal_analyst

Or describe what you need:
describe: "Data analyst who creates charts from CSVs"

AI designs the agent. You write one sentence.

---

3/5
What this means:

Claude can now run code, search the web, read files — as ONE STEP in a larger pipeline.

The next step can use Mistral (10x cheaper). Or oMLX (free, local). Or GLM-OCR (94.6% accuracy, free).

Nobody else lets you mix providers in one workflow.

---

4/5
The EU angle nobody talks about:

Mistral is EU-hosted. Your data stays in Europe.
EU AI Act compliance is built in.
Audit trail with SHA-256 hash chain.

No US competitor has this. We do. From Prague.

---

5/5
21 step types. 7 providers. 15 agent templates. 4 OCR engines.

All composable. All schedulable. All auditable.

pip install sandcastle-ai==0.30.0
https://sandcastle-ai.eu/whatsnew/
