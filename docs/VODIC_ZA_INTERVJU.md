# Vodič za intervju: Process Mining Advisor

Ovaj dokument je za tebe, da projekat možeš da objasniš i braniš na intervjuu. Pročitaj ga jednom celog, pa uradi
vežbe na kraju. Brojevi su iz `docs/eval_report.md` i iz demo firme (Panonija Trade, simulirani Q3 2026).

## 1. Priča za 30 sekundi

"Firme imaju u ERP-u trag svakog koraka: ko je napravio trebovanje, ko odobrio, kad je stigla faktura, kad je
plaćena. Process mining iz tog traga rekonstruiše kako proces stvarno teče. Napravio sam alat koji iz event loga
procesa nabavke i plaćanja nađe uska grla, ponovni rad i kršenja pravila, izračuna koliko bi automatizacija uštedela,
a AI savetnik napiše preporuke koje čovek prihvata ili odbija. Na demo firmi nađe, na primer, da se e-fakture iz
SEF-a ručno prekucavaju (oko 1.250 sati godišnje) i da plaćanje jednom nedeljno gubi popuste za ranije plaćanje
(oko 926.000 RSD godišnje). Procene proveravam ponovnom simulacijom istog kvartala sa uvedenom merom."

## 2. Kako da opišeš svoju ulogu (iskreno)

"Ja sam vlasnik projekta: izabrao sam problem i obim i postavio ciljeve. Kod je pisao AI asistent kao builder, i
on je predložio arhitekturu, a ja sam je prihvatio. Poenta je da znam kako se takav sistem postavi i orkestrira, i
da razumem i branim svaku odluku." Odluke su u `DECISIONS.md`. Nauči D2, D4, D6, D7 i D9.

## 3. Osnovni pojmovi process mininga

| Pojam | Značenje u ovom projektu |
|---|---|
| Event log | tabela: jedan red = jedan korak. Obavezno: `case_id` (jedna nabavka), `activity` (šta je urađeno), vreme. Korisno: početak, ko je radio, iznos, dobavljač |
| Slučaj (case) | jedna nabavka od trebovanja do plaćanja |
| Varijanta | redosled aktivnosti jednog slučaja; ista varijanta = isti put kroz proces |
| DFG (directly-follows graph) | mapa procesa: strelica A→B znači da je B došlo odmah posle A; na strelici broj prelaza ili čekanje |
| Process discovery | pravljenje modela iz loga. Ovde: Inductive Miner (pm4py), koji uvek daje ispravan model; "infrequent" verzija sa pragom 0,2 izbacuje retke putanje |
| Conformance checking | poređenje loga sa propisanim procesom. Ovde: token-based replay na modelu "kako treba" (to-be). Demo: 73,5% slučajeva ide propisanim putem |
| Fitness / precision | fitness = koliko log staje u model; precision = koliko model ne dozvoljava putanje kojih nema u logu |
| Vreme rada vs čekanje | rad = kraj − početak koraka; čekanje = početak koraka − kraj prethodnog. U praksi je čekanje skoro uvek veće od rada |
| Rework | isti korak više puta u istom slučaju (npr. ponovno uparivanje posle razlike u ceni) |

## 4. Kako radi, korak po korak

1. **Log.** Demo log pravi simulator (`simulate.py`): ljudi rade 8-16, menadžeri gledaju odobrenja 9-10 i 14-15,
   plaćanje izlazi samo na dane platnog naloga. Čekanje nastaje iz redova, ne iz slučajnog broja. Pravi log se učitava
   iz CSV/XES; profil (`profile.py`) kaže koja aktivnost je npr. "unos fakture".
2. **Analiza** (`analysis.py`): KPI, mapa, varijante, rework, opterećenje ljudi, rang kandidata za automatizaciju
   (ručni sati godišnje).
3. **Discovery i conformance** (`discovery.py`, pm4py): model procesa, BPMN izvoz, odstupanja od propisanog toka
   (npr. "višak: Create Purchase Order (retroactive); nedostaje: trebovanje, PO" = nabavka bez narudžbenice).
4. **Nalazi** (`findings.py`): 8 detektora, svaki meri jedan broj i poredi ga sa pragom zapisanim u kodu.
5. **Mere i uštede** (`levers.py`): za svaki nalaz mera i procena iz loga: sati, RSD, uvođenje, povrat investicije.
   Mere zavisne od ljudi imaju pretpostavku i oznaku "pretpostavka".
6. **Šta ako** (`whatif.py`): isti kvartal, isti slučajevi, nova pravila. Pokazuje i ono što procena iz loga ne vidi:
   vreme ciklusa i preklapanje mera.
7. **AI savetnik** (`advisor/`): Gemini ili Claude poziva alate, pravi preporuke koje ČEKAJU odluku i piše izveštaj na
   srpskom. Svaki broj u izveštaju se proverava (grounding).
8. **Odluka.** Samo imenovana osoba prihvata ili odbija preporuku; sve ide u audit log sa lancem heševa.

## 5. Brojevi koje treba da znaš (demo firma)

- 1.818 nabavki u kvartalu, 18.581 događaj, 49 varijanti, medijana trajanja 14,8 dana.
- Ručni rad oko 7.097 sati godišnje (4,1 FTE).
- Najveće mere: SEF→ERP integracija 1.251 h / neto 1.256.000 RSD godišnje (povrat 5,7 meseci); automatsko uparivanje
  1.146 h / 1.081.000 RSD (10 meseci); dnevni platni nalog 926.000 RSD popusta (praktično odmah).
- Simulacija paketa (SEF, uparivanje, plaćanje, eskalacija, duplikati, cenovnici): ručni rad 7.097 → 4.431 h,
  prosečno trajanje 14,2 → 10,8 dana, propušteni popusti 1.051.000 → 67.000 RSD godišnje.
- Pravilo "nema narudžbenice, nema plaćanja" NE štedi vreme (−51 h): to je kontrolna mera, i alat to kaže.

Evaluacija (sintetički podaci, vidi `docs/eval_report.md`): detektori nalaze 100% ubačenih problema bez lažnih alarma na jasnim, "zašumljenim" logovima i logovima bez
vremena početka; na blagim problemima blizu pragova 77,5% (usko grlo u odobravanju samo 9%, jer odobravalac koji radi
3 dana nedeljno ostaje ispod praga od 8 h). Procene iz loga vs simulacija: 0-2% greške za mehaničke mere, 5-7% za
procesne. AI savetnik (Gemini 3.5 Flash, 3 scenarija, mali uzorak): 15 od 15 problema pokriveno preporukom, 0
suvišnih preporuka, 183 od 183 broja potvrđeno u rezultatima alata.

Budi iskren o ograničenjima: podaci su sintetički, simulator i detektore je pisao isti builder (pa 100% na
"jasnim" problemima pokazuje da logika radi, a ne da važi za svaku firmu), troškovi uvođenja su grube procene.
Pravi broj daje pilot na logu firme.

## 6. Pitanja i odgovori

**Po čemu se ovo razlikuje od Celonis-a?**
Celonis je platforma za velike firme. Ovo je mali, otvoren alat koji pokazuje ceo lanac: log → model → nalaz →
procena uštede → preporuka → odluka čoveka. Za srednju firmu u Srbiji to je realan prvi korak pre skupe platforme.

**Zašto ne pustiš LLM da sam analizira log?**
Zato što bi brojao i računao napamet, a 18.000 događaja ne staje pametno u prompt. Brojeve računa kod (pandas,
pm4py), pravila su zapisana, a model radi ono što radi dobro: bira šta je važno, povezuje nalaze i piše izveštaj.
Provera brojeva (grounding) hvata svaki broj koji nije iz alata.

**Kako znaš da su procene ušteda tačne?**
Za mehaničke mere (nema više ručnog unosa) procena je skoro tačna po konstrukciji. Zato postoji šta-ako simulacija:
ona preračuna redove i rasporede. Prva verzija procene za eskalaciju odobrenja grešila je oko 40%, jer je
pretpostavila da zamenik reaguje za sat, a rok od 24 h često ističe noću ili za vikend. Popravio sam je da koristi
sate u nedelji kada odobravaoci stvarno rade (iz loga), i greška je pala na oko 2%. To je dobar primer zašto se
procena proverava.

**Zašto je zbir ušteda u KPI kartici "zbir"?**
Mere se preklapaju: automatsko uparivanje briše i drugo uparivanje posle razlike u ceni, koje računa i mera za
cenovnike. Tačan zajednički efekat daje simulacija paketa.

**Šta ako log nema vreme početka?**
Onda nema vremena rada, samo čekanja između koraka. Alat to prijavi, a svaki korak koji radi osoba računa kao ručni.
Evaluacija pokazuje da detektori i tada nađu probleme.

**Šta bi radio sa pravom firmom?**
(1) Izvoz loga iz ERP-a (SAP: tabele EKKO/EKPO, MSEG, RBKP; Pantheon/drugi: izveštaj promena dokumenata).
(2) Profil sa nazivima aktivnosti i provera pragova sa ljudima iz finansija. (3) Simulacioni model kalibrisan na
njihovom logu za šta-ako. (4) Pilot jedne mere (npr. SEF integracija) i merenje pre/posle.

**Licenca?**
pm4py je AGPL-3.0. Za zatvoren komercijalni proizvod treba komercijalna licenca ili zamena modula `discovery.py`.
To je važno ako bih ovo radio kao freelancer za klijenta.

## 7. Vežbe pre intervjua

1. `pma analyze` i pročitaj nalaze. Za svaki nađi u `findings.py` prag koji ga je okinuo.
2. `pma serve`, otvori Mapu procesa, prebaci na "čekanje" i nađi crvene strelice. Objasni zašto je
   Approve Invoice → Pay Invoice crveno.
3. U "Šta ako" uključi samo "Svakodnevni platni nalog". Zašto se vreme ciklusa skrati za oko 2 dana a ručni sati ne
   promene?
4. `pma analyze --issues manual_three_way_match --seed 3`: firma sa samo jednim problemom. Šta alat kaže?
5. Pokreni savetnika sa svojim Gemini ključem, pa prihvati jednu i odbij jednu preporuku. Otvori Audit.
6. Objasni nekome u 2 minuta razliku između vremena rada i čekanja, i zašto automatizacija ručnog koraka ne
   skraćuje uvek trajanje procesa (vežba 3 je odgovor).
