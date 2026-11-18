- **Python:** CPython 3.13.7
- **GIL:** włączony
- **System:** Windows 10
- **Rdzenie CPU:** 24
- **Workers (CPU / I/O):** 8 / 16
- **Powtórzenia:** 3

| Obciążenie | Wariant | Najlepszy czas [s] | Mediana [s] | Przyspieszenie |
|---|---|---:|---:|---:|
| CPU | sequential | 4.061 | 4.075 | 1.00× |
| CPU | threads | 4.264 | 4.275 | 0.95× |
| CPU | processes | 0.716 | 0.718 | 5.67× |
| I/O | sequential | 2.112 | 2.114 | 1.00× |
| I/O | threads (Queue+Lock) | 0.140 | 0.142 | 15.13× |
| I/O | ThreadPoolExecutor | 0.138 | 0.138 | 15.36× |
| I/O | asyncio | 0.201 | 0.202 | 10.52× |
| I/O | processes | 0.381 | 0.396 | 5.54× |
