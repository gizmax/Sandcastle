#!/usr/bin/env python3
"""Generate the demo PID report v4 showcasing all visual features.

This script creates a comprehensive PDF report about Prague Integrated
Transport (PID) using auto-charts, explicit chart blocks, KPI metric rows,
callout boxes, and styled dividers.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the source tree is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sandcastle.engine.dag import ReportConfig  # noqa: E402
from sandcastle.engine.report import render_report  # noqa: E402

ACCENT = "#0ea5e9"  # Sky blue for a transport theme

MARKDOWN_CONTENT = r"""

# Prazska integrovana doprava (PID) - Komplexni analyza

:::metric 1,2 mld. cestujících ročně | 65 km metra | 142 km tramvají | 826 autobusových linek:::

## Úvod do systému PID

Pražská integrovaná doprava (PID) představuje jeden z nejrozsáhlejších systémů městské hromadné dopravy ve střední Evropě. Systém propojuje metropoli Prahu s rozsáhlým zázemím Středočeského kraje a zajišťuje denní přepravu více než tří milionů cestujících.

:::info
PID byl založen v roce 1992 jako první integrovaný dopravní systém v České republice. Od svého vzniku prošel zásadní transformací - z původního systému pokrývajícího pouze Prahu se rozrostl do celého Středočeského kraje.
:::

Systém PID je unikátní svou komplexností a provázaností jednotlivých druhů dopravy. Metro, tramvaje, autobusy, trolejbusy, přívozy a železniční linky tvoří koherentní celek, který umožňuje cestujícím plynulý přestup mezi jednotlivými druhy dopravy s jedinou jízdenkou.

V posledních letech došlo k masivní modernizaci celého systému. Nové soupravy metra, klimatizované tramvaje a nízkopodlažní autobusy zásadně zvýšily komfort cestování. Digitalizace přinesla mobilní aplikace, bezkontaktní platby a informační systémy v reálném čase.

---{Přehled dopravních prostředků}---

## Metro - páteř pražské dopravy

Pražské metro je bezesporu nejdůležitější součástí městské hromadné dopravy. Se třemi linkami (A, B, C) a 61 stanicemi přepraví denně přes 1,2 milionu cestujících. Metro je známé svou spolehlivostí - průměrná přesnost příjezdu dosahuje 99,3 %.

:::metric 61 stanic | 65,2 km tratí | 1,6 mil. cestujících denně | 99,3 % přesnost:::

### Statistiky přepravy podle linek

| Linka | Cestujících denně | Délka (km) | Počet stanic | Interval špička (min) |
|-------|-------------------|------------|--------------|----------------------|
| A (zelená) | 520000 | 17.1 | 17 | 2.0 |
| B (žlutá) | 430000 | 25.7 | 24 | 2.5 |
| C (červená) | 650000 | 22.4 | 20 | 2.0 |

Linka C je historicky nejstarší a zároveň nejvytíženější linkou pražského metra. Její trasa propojuje sídliště na jihu a severu města s centrem, včetně hlavního nádraží a Florenc. Linka A obsluhuje trasu západ-východ včetně letiště (prodloužení z roku 2015). Linka B je nejdelší a pokrývá široký oblouk přes střed města.

### Modernizace vozového parku

V rámci obnovy vozového parku probíhá dodávka nových souprav od konsorcia Škoda-Siemens. Tyto moderní vlaky disponují klimatizací, Wi-Fi připojením, USB nabíjením a dynamickými informačními panely.

| Typ soupravy | Rok výroby | Kapacita | Rychlost (km/h) | Klimatizace |
|-------------|-----------|---------|-----------------|-------------|
| 81-71M | 1998 | 1200 | 80 | Ne |
| M1 | 2000 | 1350 | 80 | Částečná |
| Metro NB | 2024 | 1500 | 90 | Ano |

:::warning
Stanice Můstek (přestup A/B) dosahuje v dopravní špičce kapacitního limitu. Plánovaná rekonstrukce eskalátorů (2027-2028) dočasně sníží průchodnost o 30 %. Doporučuje se využívat alternativní přestupní stanici Muzeum (A/C).
:::

### Plánovaný rozvoj metra

Nejvýznamnějším projektem je výstavba linky D, která propojí centrum města s jižními sídlišti. Stavba první etapy (Pankrác - Nové Dvory) byla zahájena v roce 2024 s plánovaným zprovozněním v roce 2029.

| Etapa | Úsek | Délka (km) | Stanice | Předpokládané náklady (mld. Kč) |
|-------|------|-----------|---------|-------------------------------|
| I | Pankrác - Nové Dvory | 10.6 | 10 | 82.5 |
| II | Náměstí Míru - Pankrác | 3.8 | 3 | 34.2 |
| III | Nové Dvory - Depo Písnice | 2.1 | 2 | 18.7 |

---{Tramvajová doprava}---

## Tramvajová síť

Pražská tramvajová síť patří mezi nejrozsáhlejší na světě. S délkou 142 km tratí a 34 denními linkami tvoří nezbytný doplněk metra a zajišťuje obsluhu oblastí mimo dosah podzemní dráhy.

:::metric 34 denních linek | 142 km tratí | 820 tis. cestujících denně | 9 nočních linek:::

### Vytíženost tramvajových linek

| Linka | Trasa | Cestujících denně | Délka trasy (km) | Interval (min) |
|-------|-------|-------------------|-------------------|----------------|
| 22 | Bílá Hora - Nádraží Hostivař | 85000 | 19.8 | 4 |
| 9 | Sídliště Řepy - Spojovací | 62000 | 15.2 | 5 |
| 17 | Sídliště Modřany - Vozovna Kobylisy | 58000 | 18.4 | 5 |
| 3 | Sídliště Modřany - Kobylisy | 52000 | 16.9 | 6 |
| 14 | Sídliště Modřany - Vozovna Kobylisy | 48000 | 17.1 | 6 |

Linka 22 je ikonická - prochází historickým centrem města včetně Pražského hradu a Malé Strany. Je oblíbená jak u místních obyvatel, tak u turistů. V letních měsících přepraví až 110 000 cestujících denně.

```chart
type: bar
title: Denní přeprava podle druhu dopravy
data:
  Metro: 1600000
  Tramvaje: 820000
  Autobusy: 650000
  Přívozy: 8500
  Lanovka: 4200
```

### Modernizace tramvajové flotily

Praha systematicky obnovuje svůj vozový park tramvají. Nové tramvaje Škoda ForCity Smart 15T a jejich nástupce 52T nahrazují dosluhující vozy T3 a T6A5.

| Model | Počet vozů | Rok dodávky | Kapacita | Nízkopodlažní |
|-------|-----------|------------|---------|---------------|
| T3R.P | 248 | 1990-2005 | 165 | Ne |
| 14T | 60 | 2006-2010 | 182 | Částečně |
| 15T ForCity | 250 | 2010-2024 | 178 | Ano |
| 52T ForCity Smart | 40 | 2024-2026 | 195 | Ano |

:::success
V roce 2025 dosáhl podíl nízkopodlažních tramvají v Praze 78 %, což výrazně překračuje legislativní požadavek EU (50 % do roku 2025). Praha je v tomto ohledu evropským lídrem.
:::

### Nové tramvajové tratě

V období 2024-2030 je plánována výstavba několika nových tramvajových tratí, které rozšíří dostupnost tramvajové dopravy do dosud neobsluhovaných oblastí.

| Trať | Délka (km) | Investice (mil. Kč) | Plánované dokončení |
|------|-----------|-------------------|-------------------|
| Modřany - Libuš | 3.2 | 1850 | 2026 |
| Sídliště Barrandov - Holyně | 2.8 | 1620 | 2027 |
| Podbaba - Suchdol | 4.5 | 2780 | 2028 |
| Dvorce - Budějovická | 2.1 | 1340 | 2029 |
| Počernická - Štěrboholy | 3.7 | 2150 | 2030 |

---{Autobusová doprava}---

## Autobusová doprava

Autobusy tvoří nejflexibilnější složku PID. S 826 linkami (z toho 178 městských a 648 příměstských) pokrývají území, kam metro ani tramvaje nedosáhnou. Příměstské linky zajišťují propojení Prahy s celým Středočeským krajem.

:::metric 826 linek | 650 tis. cestujících denně | 178 městských linek | 648 příměstských linek:::

### Přepravní výkony autobusové dopravy

| Provozovatel | Linek | Cestujících/den | Vozidel | Podíl nízkopodlažních (%) |
|-------------|-------|----------------|---------|--------------------------|
| DPP | 178 | 420000 | 1280 | 92 |
| ROPID příměstské | 648 | 230000 | 1850 | 78 |
| Celkem | 826 | 650000 | 3130 | 83 |

### Elektrifikace autobusové flotily

Praha se zavázala k úplné elektrifikaci městské autobusové dopravy do roku 2035. V současnosti je v provozu 120 elektrobusů a 15 vodíkových autobusů.

| Typ pohonu | Počet vozidel | Podíl (%) | Cílový stav 2035 |
|-----------|--------------|----------|-----------------|
| Diesel Euro VI | 980 | 76.5 | 0 |
| CNG | 180 | 14.1 | 0 |
| Elektrobus | 120 | 9.4 | 1100 |
| Vodíkový | 15 | 1.2 | 180 |

:::info
V roce 2025 Praha objednala dalších 200 elektrobusů od konsorcia SOR/Škoda Electric. Dodávky budou probíhat v letech 2026-2028. Celková investice činí 5,2 miliardy Kč.
:::

```chart
type: pie
title: Struktura pohonů autobusové flotily (2025)
data:
  Diesel Euro VI: 76.5
  CNG: 14.1
  Elektrobus: 9.4
  Vodíkový: 1.2
```

---{Ekonomické ukazatele}---

## Ekonomika PID

Provoz systému PID je financován kombinací tržeb z jízdného, dotací z rozpočtu hlavního města Prahy, Středočeského kraje a státních dotací. Celkové provozní náklady systému dosahují přibližně 32 miliard Kč ročně.

:::metric 32 mld. Kč provozní náklady | 12 mld. Kč tržby z jízdného | 37,5 % pokrytí tržbami | 2,8 mld. Kč investice:::

### Roční rozpočet PID

| Položka | Příjmy (mld. Kč) | Výdaje (mld. Kč) |
|---------|------------------|------------------|
| Jízdné | 12.0 | - |
| Reklama a pronájmy | 1.8 | - |
| Dotace Praha | - | 15.2 |
| Dotace Středočeský kraj | - | 4.8 |
| Státní dotace | - | 3.5 |
| Provoz metra | - | 8.6 |
| Provoz tramvají | - | 7.2 |
| Provoz autobusů | - | 9.4 |
| Provoz železnice PID | - | 6.8 |

### Vývoj cen jízdného

| Rok | Jednorázová 90 min (Kč) | Měsíční kupon (Kč) | Roční kupon (Kč) |
|-----|------------------------|-------------------|-----------------|
| 2018 | 32 | 550 | 3650 |
| 2019 | 32 | 550 | 3650 |
| 2020 | 32 | 550 | 3650 |
| 2021 | 32 | 550 | 3650 |
| 2022 | 40 | 550 | 3650 |
| 2023 | 40 | 550 | 3650 |
| 2024 | 40 | 550 | 3650 |
| 2025 | 45 | 650 | 4750 |

:::warning
K 1. 1. 2025 došlo k výraznému zdražení jízdného. Jednorázová jízdenka na 90 minut se zvýšila z 40 na 45 Kč (+12,5 %). Roční kupon vzrostl z 3 650 na 4 750 Kč (+30 %). Zdražení bylo nezbytné kvůli rostoucím provozním nákladům a inflaci.
:::

```chart
type: line
title: Vývoj ceny ročního kuponu PID (Kč)
data:
  2018: 3650
  2019: 3650
  2020: 3650
  2021: 3650
  2022: 3650
  2023: 3650
  2024: 3650
  2025: 4750
```

### Srovnání s evropskými městy

Přestože Praha zdražila jízdné, stále patří mezi nejlevnější systémy MHD v Evropě:

| Město | Roční kupon (EUR) | Délka metra (km) | Obyvatel (mil.) |
|-------|------------------|-----------------|----------------|
| Praha | 190 | 65 | 1.35 |
| Varšava | 260 | 38 | 1.86 |
| Vídeň | 365 | 83 | 1.91 |
| Berlín | 761 | 155 | 3.75 |
| Paříž | 925 | 226 | 2.16 |
| Londýn | 2100 | 402 | 8.98 |

---{Technologie a inovace}---

## Technologické inovace

Digitalizace a modernizace technologické infrastruktury je jedním z klíčových pilířů rozvoje PID. V posledních letech byly implementovány systémy, které zásadně mění způsob, jakým cestující interagují s dopravním systémem.

:::metric 2,4 mil. uživatelů aplikace PID Lítačka | 78 % bezkontaktních plateb | 4 200 displejů na zastávkách | 99,7 % pokrytí GPS:::

### Bezkontaktní platby a mobilní aplikace

Systém PID Lítačka umožňuje cestujícím platit jízdné bezkontaktní bankovní kartou nebo mobilním telefonem. Od zavedení v roce 2019 se stal jedním z nejúspěšnějších projektů v Evropě.

| Platební metoda | Podíl na tržbách (%) | Počet transakcí/den |
|----------------|---------------------|-------------------|
| PID Lítačka (kupony) | 42 | 850000 |
| Bezkontaktní karta | 28 | 680000 |
| Papírová jízdenka | 15 | 320000 |
| SMS jízdenka | 8 | 180000 |
| Mobilní aplikace | 7 | 150000 |

:::success
Praha je první město ve střední Evropě, které zavedlo plně integrovaný systém bezkontaktních plateb ve všech druzích MHD. Systém byl oceněn cenou UITP Innovation Award 2024.
:::

### Informační systémy v reálném čase

Všechny zastávky jsou vybaveny GPS sledováním vozidel a dynamickými informačními panely zobrazujícími skutečné příjezdy. Systém zpracovává přes 50 milionů datových bodů denně.

| Technologie | Pokrytí (%) | Rok zavedení | Investice (mil. Kč) |
|------------|------------|-------------|-------------------|
| GPS sledování vozidel | 99.7 | 2015 | 280 |
| Dynamické info panely | 94.2 | 2017 | 420 |
| Wi-Fi ve vozidlech | 78.5 | 2019 | 350 |
| USB nabíjení | 65.3 | 2020 | 180 |
| Kamerový systém | 100.0 | 2022 | 520 |

---{Železniční doprava v PID}---

## Železniční linky PID

Integrace příměstské železniční dopravy do systému PID představuje významný milník. V současnosti je do PID začleněno 38 železničních linek označených jako S-linky (S1-S38), které propojují Prahu s celým Středočeským krajem.

:::metric 38 S-linek | 185 tis. cestujících denně | 580 km tratí | 156 železničních stanic:::

### Nejvytíženější S-linky

| Linka | Trasa | Cestujících/den | Interval špička (min) |
|-------|-------|----------------|---------------------|
| S1 | Masarykovo n. - Kolín | 28500 | 15 |
| S9 | Masarykovo n. - Benešov | 24200 | 20 |
| S7 | Masarykovo n. - Beroun | 22800 | 20 |
| S41 | Praha hl.n. - Kralupy n.Vlt. | 18600 | 30 |
| S49 | Praha hl.n. - Kladno | 16900 | 30 |

### Železniční uzly a přestupní terminály

Klíčovou roli v systému PID hrají železniční uzly, kde dochází k přestupům mezi vlaky, metrem, tramvajemi a autobusy.

| Uzel | Denní cestující | Přestupní vazby | Rekonstrukce |
|------|----------------|----------------|-------------|
| Praha hl.n. | 145000 | Metro C, tramvaje, autobusy | 2025-2028 |
| Masarykovo n. | 85000 | Metro B, tramvaje | Dokončena 2024 |
| Praha-Smíchov | 62000 | Metro B, tramvaje, autobusy | 2026-2029 |
| Praha-Vysočany | 38000 | Metro B, tramvaje, autobusy | Plánována |
| Praha-Holešovice | 35000 | Metro C, tramvaje, autobusy | 2027-2030 |

```chart
type: horizontal_bar
title: Denní cestující v železničních uzlech
data:
  Praha hl.n.: 145000
  Masarykovo n.: 85000
  Praha-Smíchov: 62000
  Praha-Vysočany: 38000
  Praha-Holešovice: 35000
```

---{Bezpečnost a životní prostředí}---

## Bezpečnost v dopravě

Bezpečnost cestujících je jednou z hlavních priorit PID. Systém zahrnuje komplexní bezpečnostní opatření od kamerových systémů přes nouzová volání až po fyzickou přítomnost bezpečnostního personálu.

:::metric 12 500 kamer | 850 bezpečnostních pracovníků | 3 500 nouzových hlásičů | 98,2 % spokojenost:::

### Bezpečnostní incidenty (roční přehled)

| Kategorie | Počet 2023 | Počet 2024 | Změna (%) |
|-----------|----------|----------|----------|
| Kapesní krádeže | 2850 | 2420 | -15.1 |
| Vandalizmus | 1680 | 1340 | -20.2 |
| Napadení personálu | 245 | 198 | -19.2 |
| Jízda bez jízdenky | 185000 | 162000 | -12.4 |
| Technické poruchy | 3200 | 2850 | -10.9 |

:::success
Zavedení inteligentního kamerového systému s AI analýzou obrazu v roce 2023 vedlo k výraznému snížení bezpečnostních incidentů ve všech kategoriích. Celkový pokles činí 15,6 % meziročně.
:::

## Environmentální dopady

Městská hromadná doprava hraje klíčovou roli ve snižování emisí skleníkových plynů a zlepšování kvality ovzduší v Praze. PID aktivně přechází na ekologičtější provoz.

### Emise CO2 podle druhu dopravy

| Druh dopravy | g CO2/cestující-km | Roční emise (tis. tun) | Podíl (%) |
|-------------|-------------------|----------------------|----------|
| Metro | 5.2 | 28.4 | 8.5 |
| Tramvaje | 8.1 | 22.6 | 6.8 |
| Trolejbusy | 6.4 | 3.1 | 0.9 |
| Autobusy diesel | 68.5 | 245.3 | 73.5 |
| Autobusy elektro | 4.8 | 2.8 | 0.8 |
| Příměstské vlaky | 22.3 | 31.6 | 9.5 |

:::info
Přechodem na elektrobusy do roku 2035 Praha ušetří přibližně 240 000 tun CO2 ročně. To odpovídá emisím z 52 000 osobních automobilů. Investice ve výši 28 miliard Kč se tak promítne do významného zlepšení kvality ovzduší.
:::

```chart
type: pie
title: Podíl jednotlivých druhů dopravy na emisích CO2
data:
  Autobusy diesel: 73.5
  Příměstské vlaky: 9.5
  Metro: 8.5
  Tramvaje: 6.8
  Trolejbusy: 0.9
  Autobusy elektro: 0.8
```

---{Přístupnost a inkluze}---

## Přístupnost pro osoby se sníženou pohyblivostí

PID klade velký důraz na bezbariérovost a přístupnost pro všechny skupiny cestujících. Systematická modernizace infrastruktury přináší výrazné zlepšení.

### Bezbariérovost stanic metra

| Linka | Celkem stanic | Bezbariérových | Podíl (%) | Plán 2030 (%) |
|-------|--------------|---------------|----------|--------------|
| A | 17 | 14 | 82.4 | 100 |
| B | 24 | 18 | 75.0 | 95 |
| C | 20 | 16 | 80.0 | 100 |
| Celkem | 61 | 48 | 78.7 | 98 |

### Bezbariérové zastávky podle typu

| Typ zastávky | Celkem | Bezbariérových | Podíl (%) |
|-------------|-------|---------------|----------|
| Metro | 61 | 48 | 78.7 |
| Tramvajové | 624 | 485 | 77.7 |
| Autobusové městské | 2180 | 1740 | 79.8 |
| Autobusové příměstské | 3420 | 2150 | 62.9 |
| Železniční | 156 | 89 | 57.1 |

---{Spokojenost cestujících}---

## Průzkumy spokojenosti

Pravidelné průzkumy spokojenosti cestujících ukazují, že PID je hodnocen nadprůměrně ve srovnání s evropskými systémy. Celková spokojenost v roce 2024 dosáhla 7,8 bodu z 10.

:::metric 7,8 / 10 celková spokojenost | 8,4 čistota vozů | 8,1 přesnost spojů | 6,9 cena jízdného:::

### Spokojenost podle oblasti

| Oblast hodnocení | Skóre 2023 | Skóre 2024 | Změna |
|-----------------|-----------|-----------|------|
| Celková spokojenost | 7.5 | 7.8 | +0.3 |
| Čistota vozidel | 8.1 | 8.4 | +0.3 |
| Přesnost spojů | 7.9 | 8.1 | +0.2 |
| Informovanost | 7.2 | 7.6 | +0.4 |
| Bezpečnost | 7.8 | 8.0 | +0.2 |
| Pohodlí | 7.0 | 7.3 | +0.3 |
| Cena jízdného | 7.2 | 6.9 | -0.3 |
| Bezbariérovost | 6.5 | 6.8 | +0.3 |

:::warning
Jediná oblast se zhoršeným hodnocením je cena jízdného (-0,3 bodu). To přímo koreluje se zdražením k 1. 1. 2025. Doporučuje se zvážit kompenzační opatření pro sociálně slabé skupiny cestujících.
:::

---{Strategický výhled}---

## Strategický plán 2025-2035

Dlouhodobý strategický plán PID definuje klíčové směry rozvoje pro nadcházející dekádu. Mezi hlavní priority patří dokončení linky D metra, úplná elektrifikace autobusů a další rozšíření integrovaného systému.

### Investiční plán podle oblastí

| Oblast investice | 2025-2027 (mld. Kč) | 2028-2030 (mld. Kč) | 2031-2035 (mld. Kč) | Celkem (mld. Kč) |
|-----------------|---------------------|---------------------|---------------------|------------------|
| Metro (linka D) | 35.5 | 42.8 | 57.1 | 135.4 |
| Tramvajové tratě | 4.2 | 5.8 | 3.5 | 13.5 |
| Autobusová flotila | 5.2 | 8.4 | 14.6 | 28.2 |
| IT a digitalizace | 1.8 | 2.1 | 2.5 | 6.4 |
| Železniční integrace | 3.5 | 4.2 | 5.8 | 13.5 |
| Bezbariérovost | 2.8 | 3.1 | 2.4 | 8.3 |

```chart
type: bar
title: Investiční plán PID 2025-2035 (mld. Kč)
data:
  Metro (linka D): 135.4
  Autobusová flotila: 28.2
  Tramvajové tratě: 13.5
  Železniční integrace: 13.5
  Bezbariérovost: 8.3
  IT a digitalizace: 6.4
```

### Klíčové milníky

| Rok | Milník | Investice (mld. Kč) |
|-----|--------|-------------------|
| 2026 | Dokončení tramvajové trati Modřany-Libuš | 1.85 |
| 2027 | 200 nových elektrobusů v provozu | 5.2 |
| 2028 | Bezbariérové 90 % stanic metra | 3.1 |
| 2029 | Zprovoznění linky D (etapa I) | 82.5 |
| 2030 | 50 % autobusů na elektrický pohon | 12.8 |
| 2032 | Úplná integrace Středočeského kraje | 4.5 |
| 2035 | 100 % elektrická autobusová flotila | 28.2 |

:::success
Celkové plánované investice do systému PID v období 2025-2035 dosahují 205 miliard Kč. Jedná se o největší investiční program v historii pražské městské dopravy, který zásadně promění podobu veřejné dopravy v Praze a okolí.
:::

## Závěr

Pražská integrovaná doprava je jedním z nejkomplexnějších a nejmodernějších systémů městské hromadné dopravy ve střední Evropě. S více než 1,2 miliardy přepravených cestujících ročně, 65 km metra, 142 km tramvajových tratí a 826 autobusovými linkami představuje páteř mobility celé pražské aglomerace.

Klíčové výzvy pro nadcházející období zahrnují:

- **Dokončení linky D metra** - největší infrastrukturní projekt v historii Prahy
- **Elektrifikace autobusové dopravy** - závazek k nulovým emisím do roku 2035
- **Rozšíření tramvajové sítě** - pět nových tratí do roku 2030
- **Digitalizace a inovace** - bezkontaktní platby, AI řízení dopravy, autonomní vozidla
- **Udržitelné financování** - hledání rovnováhy mezi cenou jízdného a kvalitou služeb
- **Bezbariérovost** - cíl 98 % bezbariérových stanic metra do roku 2030

Systém PID prokázal svou odolnost a adaptabilitu, a to i v náročných obdobích pandemie COVID-19 a energetické krize. S plánovanými investicemi ve výši 205 miliard Kč do roku 2035 se Praha řadí mezi města s nejambicióznějšími plány rozvoje veřejné dopravy v Evropě.

"""


def main() -> None:
    """Generate the demo PDF report."""
    config = ReportConfig(
        title="Pražská integrovaná doprava",
        subtitle="Komplexní analytická zpráva - stav, výkonnost a strategický plán 2025-2035",
        author="Tomas Pflanzer @gizmax",
        theme="professional",
        accent_color=ACCENT,
        include_toc=True,
        include_page_numbers=True,
        paper_size="A4",
        format="pdf",
    )

    output_path = Path(__file__).resolve().parent.parent / "demo-pid-report-v4.pdf"

    print(f"Generating report: {output_path}")
    pdf_bytes = render_report(MARKDOWN_CONTENT, config)

    output_path.write_bytes(pdf_bytes)
    print(f"Done. {len(pdf_bytes):,} bytes written to {output_path}")
    print(f"Pages: ~{len(pdf_bytes) // 15000} (estimated)")


if __name__ == "__main__":
    main()
