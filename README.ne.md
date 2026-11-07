# Investment Analyst Toolkit — नेपाली

> **अनुसन्धान र शिक्षाका लागि मात्र — लगानी सल्लाह होइन, कुनै पनि धितोपत्र
> किन्ने वा बेच्ने सिफारिस होइन।** यस पृष्ठका सबै उदाहरणहरू स्पष्ट रूपमा
> चिन्ह लगाइएको demo data प्रयोग गर्छन्, जबसम्म स्रोत उल्लेख गरिएको छैन।

**एउटा आदेश (command), कुनै कुञ्जी (key) चाहिँदैन, Python 3.10+ बाहेक अरू
केही install गर्नुपर्दैन, ६० सेकेन्डभन्दा कममा नतिजा:**

```bash
python -m toolkit.terminal demo.csv
```

यो एउटा निःशुल्क terminal हो — यसले कुनै पनि दैनिक मूल्य भएको CSV फाइललाई
indicators, setups, इमानदार Time-Machine सम्भावना (odds) र risk gate मा
बदल्छ — र हरेक सङ्ख्या पुनः उत्पादन (reproduce) गर्न सकिन्छ।

यो फाइल अङ्ग्रेजी README को सुरुआती भाग (hook, install, पहिलो run, के हो /
के होइन, licence) को नेपाली अनुवाद हो। पूरा विवरण अङ्ग्रेजी
[README.md](README.md) मा छ। कुनै प्राविधिक शब्दको राम्रो नेपाली पर्याय
नभएको ठाउँमा अङ्ग्रेजी शब्द नै राखिएको छ — ती शब्दहरू तलको टिप्पणीमा
सूचीबद्ध छन्।

## Install — ६० सेकेन्डभित्र पहिलो नतिजा

```bash
git clone https://github.com/ShirjanKhadka1/investment-analyst-toolkit.git
cd investment-analyst-toolkit
python -m toolkit.terminal demo.csv
```

`pip install` गर्ने चरण छैन: यो toolkit ले Python को standard library मात्र
प्रयोग गर्छ, त्यसैले Python 3.10+ आफैँबाहेक अरू केही install गर्नुपर्दैन।
कुनै API key चाहिँदैन, कुनै account चाहिँदैन, कुनै network चाहिँदैन।
२०२६-११-०७ मा यस मेसिनमा समय मापन गरिएको थियो: सफा clone र माथिको आदेश
समेत जम्मा **२.३ सेकेन्ड** लाग्यो। (तपाईंको मेसिन र network अनुसार फरक
पर्न सक्छ; मुख्य कुरा के हो भने पर्खनुपर्ने भनेको clone मात्र हो।)

## यो के हो

यो एउटा सानो, परीक्षण गरिएको Python toolkit हो — लगानी / अनुसन्धान
विश्लेषकले हरेक दिन गर्ने गणनाहरूका लागि: वित्तीय अनुपातहरू (ratios),
DuPont विश्लेषण, मूल्याङ्कन गुणकहरू (valuation multiples), र पारदर्शी DCF
सहायक, जसले तपाईंलाई हरेक input देखाउन बाध्य पार्छ। त्यससँगै एउटा
एक-आदेश terminal, point-in-time store, naive baseline सँग तुलना गर्ने
walk-forward model परीक्षण, अनुसन्धान प्रतिवेदन (report) pipeline र
read-only MCP server पनि छन्।

- हरेक function ले स्पष्ट inputs लिन्छ — कुनै global state छैन, कुनै जादुई
  default छैन।
- हरेक नतिजा त्यससँगै देखाइएको inputs बाट फेरि गणना गर्न सकिन्छ।
- किनाराका अवस्थाहरू (edge cases) ठूलो स्वरमा सम्हालिन्छन्: शून्यले भाग,
  नकारात्मक equity र छुटेका inputs ले चुपचाप सम्भावित देखिने सङ्ख्या
  फर्काउनुको साटो error फ्याँक्छन्।

## काम गरेको उदाहरण — `demo.csv` का वास्तविक सङ्ख्याहरू

`demo.csv` कृत्रिम (synthetic) उदाहरण data हो (८० वटा हप्ताका दिनका bars,
२०२६-०७-२० देखि २०२६-११-०६ सम्म, symbol DEMO, NPR मा NEPSE-शैलीका मूल्यहरू;
स्रोत फाइलको header मा छ)। एउटा आदेशले चार खण्ड छाप्छ:

- **Indicator snapshot** — अन्तिम close 1284.91, SMA20 (1166.08) र EMA20
  (1186.73) भन्दा माथि; RSI(14) 88.2; MACD 12/26/9 53.32, signal 41.82;
  Bollinger 20/2 %B 0.91; ATR(14) 16.79।
- **Screening** — ५ वटा नाम दिइएका setups मध्ये २ वटा मिल्छन्: `breakout_20d`
  (close 1284.91, २०-दिने उच्च 1274.72 भन्दा माथि) र `bull_flag` (+12.07%
  को वृद्धि, अन्तिम-५ को दायरा 3.24%)। मिल्नुको अर्थ विगतका close हरूले
  भनिएको नियम पूरा गर्छन् — किन्ने/बेच्ने सङ्केत होइन।
- **Time-Machine odds** — काल्पनिक योजना: entry 1284.91, stop 1259.72,
  target 1335.29 (reward:risk 2.00): १०,००० वटा seeded replay मा target
  पहिले 57.5%, stop पहिले 39.0%, कुनै पनि होइन 3.5%; विगतको replay मा
  ६१ वटा window मा 68.9% target-पहिले; औसत R 0.79। यो simulation हो,
  भविष्यवाणी होइन।
- **Risk gate** — BLOCK, १६ मध्ये १५ वटा जाँचहरू पास; एउटा BLOCK भनेको
  entry window हो, जुन पुष्टि भएको छैन। PASS हुनु भनेको "हो" भन्ने होइन।

आफ्नै फाइल पनि त्यही गरी चलाउनुहोस्: header `date,open,high,low,close,volume`
(केवल `date` र `close` अनिवार्य छन्)।

## यो के होइन

- यो live मूल्य भएको stock screener होइन (कुनै data vendor छैन, कुनै
  scraping छैन; NEPSE data तपाईंले दिनुभएको CSV बाट मात्र आउँछ)।
- यो कुनै वास्तविक कम्पनीको पूरा मूल्याङ्कन होइन। वास्तविक कम्पनीका case
  study हरू छुट्टै repo मा छन्, स्रोत उल्लेख गरेर, र हरेक input प्रमाणित
  नभएसम्म work-in-progress भनेर चिन्ह लगाइन्छ।
- यो लगानी सल्लाह होइन, र किन्ने/बेच्ने/होल्ड गर्ने सिफारिस कहिल्यै होइन।
  Setup मिल्नु वा PASS हुनु भनेको "हो" भन्ने होइन।
- यो trading प्रणाली होइन: यसले कुनै order राख्दैन, कुनै broker सँग कुरा
  गर्दैन, र कहिल्यै केही execute गर्दैन।

## Track record — इमानदार नतिजा

अहिलेसम्म कुनै पनि अनुसन्धान call लग गरेर settle गरिएको छैन — त्यसैले
track record **n=0** छ: hit rate n=0, औसत alpha n=0, wins 0 / losses 0।
विवरण: [docs/track-record.md](docs/track-record.md)। कुनै वास्तविक call
लग गरेर, त्यसको horizon पछि settle नगरेसम्म यो n=0 नै रहन्छ; खुला call हरू
कहिल्यै जितका रूपमा गनिँदैनन्, र घाटाहरू पनि रेकर्डमा रहन्छन्।

## योगदान (Contributing)

[CONTRIBUTING.md](CONTRIBUTING.md) हेर्नुहोस् — पहिले issue खोल्नुहोस्,
सबैभन्दा सानो fix गर्नुहोस्, test सहित। आचारसंहिता:
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)। सुरक्षा समस्या निजी रूपमा
रिपोर्ट गर्नुहोस्: [SECURITY.md](SECURITY.md)।

## Licence

MIT — [LICENSE](LICENSE) हेर्नुहोस्।

## टिप्पणी: अङ्ग्रेजीमै राखिएका शब्दहरू

यी प्राविधिक शब्दहरूको राम्रो नेपाली पर्याय नभएकोले अङ्ग्रेजी रूप नै
राखिएको छ: terminal, CSV, API key, indicator, setup, Time-Machine, risk
gate, Monte-Carlo, walk-forward, naive baseline, point-in-time store, MCP
server, DCF, RSI, MACD, Bollinger, ATR, SMA, EMA, pull request, issue।
