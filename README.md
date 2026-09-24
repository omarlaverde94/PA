# PA

Agente experimental que lee cuotas deportivas públicas, busca precios que no
cuadran y envía avisos por Telegram. Es una prueba en papel: no apuesta.

- Código: `scripts/` (Python 3.11).
- Se ejecuta con GitHub Actions (`.github/workflows/`).
- No guarda datos en este repositorio.
- Las claves se configuran solo como secretos del repositorio.

Pruebas:

```
cd scripts
python f5_prueba_detector.py
python f5_prueba_seguridad.py
```
