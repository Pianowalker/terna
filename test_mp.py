"""
Prueba de integración del multijugador: dos clientes WebSocket juegan una
partida completa contra el servidor real.

Uso: con el servidor corriendo en el puerto 8741,
    python test_mp.py
"""
import asyncio
import itertools
import json

import websockets

URL = 'ws://127.0.0.1:8741/ws'


def es_terna(a, b, c):
    return all((a[k] + b[k] + c[k]) % 3 == 0 for k in range(4))


def buscar_terna(cartas):
    for t in itertools.combinations(range(len(cartas)), 3):
        if es_terna(cartas[t[0]], cartas[t[1]], cartas[t[2]]):
            return list(t)
    return None


def buscar_no_terna(cartas):
    for t in itertools.combinations(range(len(cartas)), 3):
        if not es_terna(cartas[t[0]], cartas[t[1]], cartas[t[2]]):
            return list(t)
    return None


class Cliente:
    def __init__(self, nombre):
        self.nombre = nombre
        self.ws = None
        self.id = None
        self.msgs = []

    async def conectar(self):
        self.ws = await websockets.connect(URL)
        await self.enviar({'type': 'hola', 'nombre': self.nombre})

    async def enviar(self, d):
        await self.ws.send(json.dumps(d))

    async def esperar(self, tipo, timeout=3):
        """Espera (consumiendo) hasta recibir un mensaje del tipo pedido."""
        async def _loop():
            while True:
                d = json.loads(await self.ws.recv())
                self.msgs.append(d)
                if d['type'] == tipo:
                    return d
        return await asyncio.wait_for(_loop(), timeout)


async def main():
    ok = []

    def check(nombre, cond):
        ok.append((nombre, cond))
        print(('✔' if cond else '✘ FALLO'), nombre)

    ana = Cliente('Ana')
    beto = Cliente('Beto')

    # --- lobby ---
    await ana.conectar()
    b = await ana.esperar('bienvenida')
    ana.id = b['id']
    lobby = await ana.esperar('lobby')
    check('Ana entra al lobby', isinstance(lobby['mesas'], list))

    await beto.conectar()
    beto.id = (await beto.esperar('bienvenida'))['id']
    await beto.esperar('lobby')

    # --- crear y unirse ---
    await ana.enviar({'type': 'crear'})
    mesa = await ana.esperar('mesa')
    check('Ana crea mesa y es anfitriona', mesa['anfitrion'] == ana.id)

    mesa_id = None
    for _ in range(5):   # puede haber mesas viejas de otras sesiones
        lobby = await beto.esperar('lobby')
        de_ana = [m for m in lobby['mesas'] if m['anfitrion'] == 'Ana']
        if de_ana:
            mesa_id = de_ana[0]['id']
            break
    check('Beto ve la mesa de Ana en el lobby', mesa_id is not None)

    await beto.enviar({'type': 'unirse', 'mesa': mesa_id})
    mesa = await beto.esperar('mesa')
    check('Beto entra a la mesa (2 jugadores)', len(mesa['jugadores']) == 2)

    # --- Beto no puede dar inicio, Ana sí ---
    await beto.enviar({'type': 'empezar'})
    await ana.enviar({'type': 'empezar'})
    estado = await ana.esperar('estado')
    check('Empieza solo cuando lo pide la anfitriona',
          estado['estado'] == 'jugando' and len(estado['cartas']) >= 12)
    await beto.esperar('estado')

    # --- reclamo incorrecto: congela ---
    malas = buscar_no_terna(estado['cartas'])
    await beto.enviar({'type': 'reclamo', 'cartas': malas,
                       'valores': [estado['cartas'][i] for i in malas]})
    cong = await beto.esperar('congelado')
    check('Terna incorrecta congela a Beto', cong['segundos'] == 5)

    # --- congelado: sus reclamos válidos se ignoran ---
    buenas = buscar_terna(estado['cartas'])
    await beto.enviar({'type': 'reclamo', 'cartas': buenas,
                       'valores': [estado['cartas'][i] for i in buenas]})
    await asyncio.sleep(0.3)

    # --- Ana reclama la misma terna y se la lleva ---
    await ana.enviar({'type': 'reclamo', 'cartas': buenas,
                      'valores': [estado['cartas'][i] for i in buenas]})
    estado = await ana.esperar('estado')
    puntos_ana = [x['puntos'] for x in estado['jugadores'] if x['id'] == ana.id][0]
    puntos_beto = [x['puntos'] for x in estado['jugadores'] if x['id'] == beto.id][0]
    check('Ana gana la terna (+3) y Beto congelado no', puntos_ana == 3 and puntos_beto == 0)

    # --- reclamo viejo (valores que ya no están): se ignora sin congelar ---
    viejas = [[9, 9, 9, 9]] * 3   # valores imposibles
    await ana.enviar({'type': 'reclamo', 'cartas': [0, 1, 2], 'valores': viejas})
    await asyncio.sleep(0.3)
    check('Reclamo desactualizado no congela',
          not any(m['type'] == 'congelado' for m in ana.msgs))

    # --- jugar hasta el final ---
    fin = None
    for _ in range(40):
        t = buscar_terna(estado['cartas'])
        if t is None:
            break
        await ana.enviar({'type': 'reclamo', 'cartas': t,
                          'valores': [estado['cartas'][i] for i in t]})
        estado = await ana.esperar('estado')
        if estado['estado'] == 'terminada':
            fin = await ana.esperar('fin')
            break
    check('La partida termina y llega el ranking', fin is not None)
    if fin:
        check('Ranking ordenado con Ana primera',
              fin['ranking'][0]['nombre'] == 'Ana' and fin['ranking'][0]['puntos'] > 0)
        total = sum(r['puntos'] for r in fin['ranking'])
        check('Las cartas capturadas suman múltiplo de 3', total % 3 == 0)

    # --- revancha ---
    await beto.esperar('fin')
    await ana.enviar({'type': 'empezar'})
    estado = await ana.esperar('estado')
    check('Revancha: arranca de nuevo con puntajes en 0',
          estado['estado'] == 'jugando'
          and all(x['puntos'] == 0 for x in estado['jugadores']))

    # --- Ana se va en plena partida: Beto hereda la mesa ---
    await ana.enviar({'type': 'salir'})
    # Beto tiene en cola todos los estados de la partida: avanzar hasta el
    # que refleja la salida de Ana (queda un solo jugador en la mesa).
    for _ in range(60):
        estado = await beto.esperar('estado')
        if len(estado['jugadores']) == 1:
            break
    check('Beto queda como anfitrión al irse Ana',
          len(estado['jugadores']) == 1 and estado['anfitrion'] == beto.id)

    await ana.ws.close()
    await beto.ws.close()

    fallos = [n for n, c in ok if not c]
    print(f'\n{len(ok) - len(fallos)}/{len(ok)} pruebas pasaron')
    if fallos:
        raise SystemExit(1)


asyncio.run(main())
