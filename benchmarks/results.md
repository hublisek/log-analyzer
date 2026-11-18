- **Python:** CPython 3.13.7
- **GIL:** włączony
- **System:** Windows 10
- **Rdzenie CPU:** 24
- **Workers (CPU / I/O):** 8 / 16
- **Powtórzenia:** 3

| Obciążenie | Wariant | Najlepszy czas [s] | Mediana [s] | Przyspieszenie |
|---|---|---:|---:|---:|
| CPU | sequential | 4.032 | 4.297 | 1.00× |
| CPU | threads | 8.040 | 8.108 | 0.50× |
| CPU | processes | 0.737 | 0.765 | 5.47× |
| I/O | sequential | 2.118 | 2.126 | 1.00× |
| I/O | threads (Queue+Lock) | 0.140 | 0.141 | 15.13× |
| I/O | ThreadPoolExecutor | 0.140 | 0.140 | 15.12× |
| I/O | asyncio | 0.201 | 0.202 | 10.55× |
| I/O | processes | 0.407 | 0.409 | 5.20× |
