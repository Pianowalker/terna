"""
Servidor de Terna — lobby y partidas multijugador en tiempo real.

Cómo funciona, a grandes rasgos:
- FastAPI sirve la página (index.html) y abre un canal WebSocket en /ws.
- Cada navegador conectado mantiene UN WebSocket abierto. Por ese canal
  viajan mensajes JSON en ambos sentidos (ej: {"type": "crear"}).
- El servidor es la única autoridad: guarda el mazo, las cartas en mesa y
  los puntos. Los clientes solo dibujan lo que el servidor les manda.
  Así, cuando dos jugadores marcan una terna casi al mismo tiempo, gana el
  que llegó primero al servidor, sin discusiones.
- Todo vive en memoria (sin base de datos): si se reinicia el servidor,
  se pierden las partidas en curso. Para jugar entre amigos alcanza.

Para ejecutarlo:
    python -m uvicorn server:app --host 0.0.0.0 --port 8741
y entrar a http://localhost:8741 (o la IP de esta máquina, desde el celular).
"""

import itertools
import json
import random
import string
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

BASE = Path(__file__).parent
app = FastAPI()

CONGELADO_SEG = 5     # segundos sin poder jugar tras marcar una terna incorrecta
MAX_JUGADORES = 8     # tope por mesa


# ---------------------------------------------------------------------------
# Lógica del juego (espejo exacto de la del cliente en index.html)
# ---------------------------------------------------------------------------

def crear_mazo():
    """81 cartas: [cantidad, forma, color, relleno], cada atributo vale 0, 1 o 2."""
    mazo = [[i % 3, i // 3 % 3, i // 9 % 3, i // 27 % 3] for i in range(81)]
    random.shuffle(mazo)
    return mazo


def es_terna(a, b, c):
    """En cada atributo, la suma de tres valores iguales o tres distintos
    siempre es múltiplo de 3; cualquier otra combinación, no."""
    return all((a[k] + b[k] + c[k]) % 3 == 0 for k in range(4))


def hay_terna(cartas):
    return any(es_terna(*t) for t in itertools.combinations(cartas, 3))


# ---------------------------------------------------------------------------
# Estado en memoria
# ---------------------------------------------------------------------------

class Jugador:
    def __init__(self, ws, nombre):
        self.ws = ws
        self.id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.nombre = nombre
        self.mesa = None            # None = está en el lobby
        self.puntos = 0
        self.congelado_hasta = 0.0  # reloj monotónico del servidor


class Mesa:
    def __init__(self, anfitrion):
        self.id = ''.join(random.choices(string.ascii_uppercase, k=4))
        self.anfitrion = anfitrion
        self.jugadores = [anfitrion]
        self.estado = 'esperando'   # esperando | jugando | terminada
        self.mazo = []
        self.cartas = []            # cartas a la vista

    def repartir(self, n):
        sacadas = self.mazo[:n]
        del self.mazo[:n]
        return sacadas

    def asegurar_terna(self):
        """Si no hay terna a la vista, agrega de a 3 cartas del mazo.
        Devuelve True si tuvo que agregar."""
        agrego = False
        while not hay_terna(self.cartas) and self.mazo:
            self.cartas += self.repartir(3)
            agrego = True
        return agrego


jugadores = {}   # id -> Jugador (todos los conectados)
mesas = {}       # id -> Mesa


# ---------------------------------------------------------------------------
# Envío de mensajes
# ---------------------------------------------------------------------------

async def enviar(jugador, datos):
    try:
        await jugador.ws.send_text(json.dumps(datos))
    except Exception:
        pass  # si el socket murió, la desconexión se maneja en su propio loop


def datos_lobby():
    return {
        'type': 'lobby',
        'jugadores': [j.nombre for j in jugadores.values() if j.mesa is None],
        'mesas': [{'id': m.id, 'anfitrion': m.anfitrion.nombre,
                   'cantidad': len(m.jugadores), 'maximo': MAX_JUGADORES,
                   'estado': m.estado}
                  for m in mesas.values()],
    }


async def avisar_lobby():
    """Refresca el lobby de todos los que están en él."""
    datos = datos_lobby()
    for j in jugadores.values():
        if j.mesa is None:
            await enviar(j, datos)


def lista_jugadores(mesa):
    return [{'id': j.id, 'nombre': j.nombre, 'puntos': j.puntos}
            for j in mesa.jugadores]


async def avisar_mesa(mesa):
    """Pantalla de espera de la mesa (quiénes están, quién es anfitrión)."""
    datos = {'type': 'mesa', 'id': mesa.id, 'anfitrion': mesa.anfitrion.id,
             'estado': mesa.estado, 'maximo': MAX_JUGADORES,
             'jugadores': lista_jugadores(mesa)}
    for j in mesa.jugadores:
        await enviar(j, datos)


async def avisar_estado(mesa, msg=None):
    """Estado completo de la partida: cartas, mazo y puntajes."""
    datos = {'type': 'estado', 'cartas': mesa.cartas, 'mazo': len(mesa.mazo),
             'estado': mesa.estado, 'anfitrion': mesa.anfitrion.id,
             'jugadores': lista_jugadores(mesa)}
    if msg:
        datos['msg'] = msg
    for j in mesa.jugadores:
        await enviar(j, datos)


async def avisar_texto(mesa, msg):
    """Aviso suelto que no cambia las cartas (no borra selecciones ajenas)."""
    for j in mesa.jugadores:
        await enviar(j, {'type': 'aviso', 'msg': msg})


# ---------------------------------------------------------------------------
# Acciones de los jugadores
# ---------------------------------------------------------------------------

async def crear_mesa(j):
    if j.mesa:
        return
    m = Mesa(j)
    mesas[m.id] = m
    j.mesa = m
    await avisar_mesa(m)
    await avisar_lobby()


async def unirse(j, mesa_id):
    m = mesas.get(mesa_id)
    if j.mesa or not m:
        return await enviar(j, {'type': 'error', 'msg': 'Esa mesa ya no existe'})
    if m.estado == 'jugando':
        return await enviar(j, {'type': 'error', 'msg': 'La partida ya empezó'})
    if len(m.jugadores) >= MAX_JUGADORES:
        return await enviar(j, {'type': 'error', 'msg': 'La mesa está llena'})
    j.mesa = m
    j.puntos = 0
    m.jugadores.append(j)
    await avisar_mesa(m)
    await avisar_lobby()


async def salir_mesa(j, mandar_lobby=True):
    m = j.mesa
    if not m:
        return
    m.jugadores.remove(j)
    j.mesa = None
    j.puntos = 0
    if not m.jugadores:
        del mesas[m.id]
    else:
        if m.anfitrion is j:
            m.anfitrion = m.jugadores[0]   # hereda el primero que quedó
        if m.estado == 'jugando':
            await avisar_estado(m, f'{j.nombre} abandonó la partida')
        else:
            await avisar_mesa(m)
    if mandar_lobby:
        await enviar(j, datos_lobby())
    await avisar_lobby()


async def empezar(j):
    """Solo el anfitrión puede dar inicio (o pedir revancha)."""
    m = j.mesa
    if not m or m.anfitrion is not j or m.estado == 'jugando':
        return
    m.mazo = crear_mazo()
    m.cartas = m.repartir(12)
    m.asegurar_terna()
    m.estado = 'jugando'
    for x in m.jugadores:
        x.puntos = 0
        x.congelado_hasta = 0.0
    await avisar_estado(m, '¡Empezó la partida!')
    await avisar_lobby()


async def reclamo(j, indices, valores):
    """Un jugador marcó 3 cartas. El primero que llega con una terna válida
    se las lleva; una incorrecta lo congela unos segundos."""
    m = j.mesa
    if not m or m.estado != 'jugando':
        return
    if time.monotonic() < j.congelado_hasta:
        return
    if not isinstance(indices, list) or len(set(indices)) != 3:
        return
    try:
        elegidas = [m.cartas[i] for i in indices]
    except (IndexError, TypeError):
        return  # la mesa cambió justo antes de que llegara el reclamo
    if valores != elegidas:
        # El reclamo era para cartas que otro se llevó hace un instante:
        # no es culpa del jugador, se ignora sin congelarlo.
        return

    if not es_terna(*elegidas):
        j.congelado_hasta = time.monotonic() + CONGELADO_SEG
        await enviar(j, {'type': 'congelado', 'segundos': CONGELADO_SEG})
        await avisar_texto(m, f'{j.nombre} marcó una terna incorrecta ❄️')
        return

    j.puntos += 3   # cada carta capturada vale un punto

    # Reponer: si la mesa estaba en 12, las nuevas ocupan los mismos lugares
    # (las demás cartas no se mueven); si había extras, solo se quitan.
    orden = sorted(indices)
    if len(m.cartas) <= 12 and m.mazo:
        nuevas = m.repartir(3)
        for k in range(len(nuevas)):
            m.cartas[orden[k]] = nuevas[k]
        for i in sorted(orden[len(nuevas):], reverse=True):
            del m.cartas[i]
    else:
        for i in reversed(orden):
            del m.cartas[i]

    agrego = m.asegurar_terna()
    msg = f'¡{j.nombre} se llevó una terna! +3'
    if agrego:
        msg += ' · se agregaron cartas (no había terna)'
    if not m.mazo and not hay_terna(m.cartas):
        m.estado = 'terminada'
    await avisar_estado(m, msg)

    if m.estado == 'terminada':
        ranking = sorted(
            ({'nombre': x.nombre, 'puntos': x.puntos} for x in m.jugadores),
            key=lambda r: -r['puntos'])
        for x in m.jugadores:
            await enviar(x, {'type': 'fin', 'ranking': ranking,
                             'anfitrion': m.anfitrion.id})
        await avisar_lobby()


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.get('/')
async def raiz():
    return FileResponse(BASE / 'index.html')


@app.websocket('/ws')
async def canal(ws: WebSocket):
    await ws.accept()
    j = None
    try:
        while True:
            datos = json.loads(await ws.receive_text())
            tipo = datos.get('type')

            if tipo == 'hola' and j is None:
                nombre = str(datos.get('nombre', '')).strip()[:15] or 'Anónimo'
                j = Jugador(ws, nombre)
                jugadores[j.id] = j
                await enviar(j, {'type': 'bienvenida', 'id': j.id})
                await enviar(j, datos_lobby())
                await avisar_lobby()
            elif j is None:
                continue   # ignorar todo hasta que se presente
            elif tipo == 'crear':
                await crear_mesa(j)
            elif tipo == 'unirse':
                await unirse(j, datos.get('mesa'))
            elif tipo == 'salir':
                await salir_mesa(j)
            elif tipo == 'empezar':
                await empezar(j)
            elif tipo == 'reclamo':
                await reclamo(j, datos.get('cartas'), datos.get('valores'))
    except WebSocketDisconnect:
        pass
    finally:
        if j:
            await salir_mesa(j, mandar_lobby=False)
            jugadores.pop(j.id, None)
            await avisar_lobby()
