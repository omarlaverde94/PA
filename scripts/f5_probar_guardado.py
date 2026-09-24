"""
Fase 5: comprobación del guardado de datos en el repositorio privado.

    python f5_probar_guardado.py verificar        # antes de cada tanda
    python f5_probar_guardado.py probar <carpeta>  # prueba completa, a mano

- verificar: que existan los secretos DATA_REPO y PRIVATE_REPO_TOKEN y que el
  repositorio de datos sea PRIVADO (los datos nunca van a un repositorio público).
- probar: además sube una rama de prueba vacía al repositorio de datos, la
  borra, y avisa el resultado por Telegram.

Nunca imprime el token, la dirección del repositorio de datos ni la salida
de git (podría traerlos). Solo "OK" o qué paso falló.
"""
import os
import subprocess
import sys
import time

import requests

import f5_telegram as T


def _salir(msg, avisar=False):
    print(f"FALLÓ: {msg}", flush=True)
    if avisar:
        T.enviar(f"PRUEBA — no apostar\n\nPrueba de guardado: FALLÓ ({msg}).")
    sys.exit(1)


def verificar(avisar=False):
    repo = os.environ.get("DATA_REPO", "").strip()
    token = os.environ.get("PRIVATE_REPO_TOKEN", "").strip()
    if not repo or not token:
        _salir("faltan los secretos DATA_REPO o PRIVATE_REPO_TOKEN", avisar)
    if repo == os.environ.get("GITHUB_REPOSITORY", ""):
        _salir("DATA_REPO no puede ser este mismo repositorio", avisar)
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}", timeout=30,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        _salir(f"sin conexión con GitHub ({type(e).__name__})", avisar)
    if r.status_code in (401, 403):
        _salir("el token no es válido o venció", avisar)
    if r.status_code == 404:
        _salir("el token no tiene acceso al repositorio de datos (o DATA_REPO está mal escrito)", avisar)
    if r.status_code != 200:
        _salir(f"GitHub respondió con código {r.status_code}", avisar)
    if not r.json().get("private"):
        _salir("el repositorio de datos NO es privado; no se guardará nada ahí", avisar)
    print("OK: secretos presentes y repositorio de datos privado", flush=True)


def probar(carpeta):
    verificar(avisar=True)

    def git(*args):
        return subprocess.run(["git", "-C", carpeta, *args], capture_output=True).returncode == 0

    rama = f"prueba-guardado-{int(time.time())}"
    git("config", "user.name", "agente-fase5")
    git("config", "user.email", "agente-fase5@users.noreply.github.com")
    if not (git("checkout", "-q", "-b", rama) and git("commit", "-q", "--allow-empty", "-m", "Prueba de guardado")):
        _salir("no se pudo preparar la prueba", True)
    if not git("push", "-q", "origin", f"HEAD:refs/heads/{rama}"):
        _salir("el token no puede escribir en el repositorio de datos", True)
    git("push", "-q", "origin", "--delete", rama)
    print("OK: se pudo escribir en el repositorio de datos (la rama de prueba ya se borró)", flush=True)
    T.enviar("PRUEBA — no apostar\n\nPrueba de guardado: OK. El agente puede guardar los datos en el "
             "repositorio privado.")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "verificar":
        verificar()
    elif len(sys.argv) >= 3 and sys.argv[1] == "probar":
        probar(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(2)
