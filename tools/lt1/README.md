# LT1 cross-check

LoopInsighT1 (Ulm) is MIT; this script runs inside its clone.

```bash
git clone --depth 1 https://github.com/hpeuscher/loopinsight1.git /tmp/loopinsight1
cd /tmp/loopinsight1 && npm install
mkdir -p examples/Compare && cp <this repo>/tools/lt1/compare.ts examples/Compare/
cd examples/Compare && node --loader ts-node/esm ./compare.ts
```

Scenario: default UVA/Padova T1DMS patient, CSII basal, one unannounced 50 g meal over 15 min at
t=2 h, ideal CGM, 8 h. Output `compare.json` (plasma and tissue glucose, 1-min).
