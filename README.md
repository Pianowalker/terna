# Terna

Tributo web al juego de cartas SET, con nombre, símbolos y colores propios.

## Single-player

Abrí `index.html` en cualquier navegador (no necesita servidor ni dependencias).

- 81 cartas: 4 atributos (cantidad, forma, color, relleno) × 3 valores cada uno.
- Formas: círculo, triángulo, cuadrado. Colores: azul, naranja, verde. Rellenos: contorno, rayado, sólido.
- Una **terna** son 3 cartas donde cada atributo es *todo igual* o *todo distinto*.
- Puntaje por terna según el tiempo que tardes: 100 máx., baja 2 puntos por segundo, 20 mín.
- Selección incorrecta: −10 puntos (el aviso explica qué atributo falló). Pista: −25 puntos.
- Si no hay terna en mesa se agregan 3 cartas automáticamente.
- El juego termina cuando se acaba el mazo y no quedan ternas.
- Botón "¿Cómo se juega?": tutorial interactivo de 7 pasos (conceptos con
  ejemplos visuales + ejercicios de práctica sin reloj). Los mazos de los
  ejercicios son fijos y tienen exactamente una terna cada uno.

## Multijugador

Requiere el servidor Python (`server.py`):

```
pip install -r requirements.txt
python -m uvicorn server:app --host 0.0.0.0 --port 8741
```

Entrar a `http://localhost:8741` (o a `http://<IP-de-la-PC>:8741` desde el
celular en la misma red) y tocar **Multijugador**.

- Entrás con tu nombre (sin registro; se recuerda en el navegador).
- Lobby común: ves quién está conectado y las mesas abiertas; creás una mesa
  o te unís con un clic. Hasta 8 jugadores por mesa.
- El anfitrión (👑) da inicio. Todos ven el mismo tablero: el primero que
  marca las 3 cartas de una terna válida se las lleva (3 cartas = 3 puntos).
- Terna incorrecta: quedás congelado 5 segundos (sin perder puntos).
- Sin pistas. Si no hay terna en mesa, el servidor agrega 3 cartas solo.
- Al acabarse el mazo: tabla de posiciones y opción de revancha.

El servidor es la única autoridad (mazo, validación, puntos); ante reclamos
casi simultáneos gana el que llegó primero, y un reclamo sobre cartas que
otro acaba de llevarse se descarta sin castigo.

`test_mp.py` es una prueba de integración: con el servidor corriendo,
`python test_mp.py` simula dos jugadores que juegan una partida completa.

## Estructura

- `index.html` — todo el cliente (single-player, tutorial y multijugador).
- `server.py` — servidor FastAPI + WebSockets, comentado en español.
- `test_mp.py` — prueba de integración del multijugador.
