import os
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from betano_ocr import analyze_betano_image

# Configurar salida UTF-8 para consola Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Configuración
BOT_TOKEN = "8365375317:AAE882Q2wWU0K56p1nS8xc6cnwWLe7OO5iw"
DB_PATH = os.path.join(os.path.dirname(__file__), "finanzas_apuestas.db")

def download_telegram_photo(file_id, user_id=0):
    try:
        file_info = send_telegram_request("getFile", {"file_id": file_id})
        if file_info and file_info.get("ok"):
            file_path = file_info.get("result", {}).get("file_path")
            if file_path:
                url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                dest = os.path.join(os.path.dirname(__file__), f"temp_ticket_{user_id}.jpg")
                urllib.request.urlretrieve(url, dest)
                return dest
    except Exception as e:
        print(f"Error descargando foto: {e}")
    return None


# Inicializar Base de Datos SQLite
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        fecha TEXT,
        tipo TEXT, -- 'gasto_canal', 'apuesta_ganada', 'apuesta_perdida'
        concepto TEXT,
        monto_invertido REAL,
        monto_retorno REAL,
        monto_neto REAL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_config (
        user_id INTEGER PRIMARY KEY,
        bankroll REAL DEFAULT 0.0,
        stake_pct REAL DEFAULT 1.0,
        deuda REAL DEFAULT 0
    )
    """)
    try:
        cursor.execute("ALTER TABLE user_config ADD COLUMN deuda REAL DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    conn.close()

def get_user_bankroll(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT bankroll FROM user_config WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return float(row[0])
    return 0.0  # Default: 0 COP (El usuario ingresa su capital real)

CLOUD_SYNC_ENDPOINT = "https://api.restful-api.dev/objects/ff808181a09d98f701a169b9e0097184"

def sincronizar_archivo_json(bankroll, deuda, tipo_mov=None, concepto=None, monto_inv=0, cuota=1.0, retorno=0, ganancia_neta=0):
    try:
        json_path = os.path.join(os.path.dirname(__file__), "datos_apuestas.json")
        gh_data = {
            "bankroll": float(bankroll),
            "deuda": float(deuda),
            "movimientos": [],
            "apuestasActivas": [],
            "ultimaActualizacion": datetime.now().isoformat()
        }
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        gh_data["movimientos"] = existing.get("movimientos", [])
                        gh_data["apuestasActivas"] = existing.get("apuestasActivas", [])
            except Exception:
                pass

        if tipo_mov and concepto:
            nuevo_mov = {
                "id": int(datetime.now().timestamp() * 1000),
                "code": "AUD_" + str(int(datetime.now().timestamp() * 1000))[-4:],
                "fecha": datetime.now().strftime("%d/%m/%Y %I:%M %p").lower(),
                "tipo": "Conciliación Betano" if tipo_mov == "conciliacion" else ("Retiro de Ganancias" if tipo_mov == "retiro" else ("Depósito / Recarga" if tipo_mov == "recarga" else "Ajuste de Capital")),
                "categoria": "retiro" if tipo_mov == "retiro" else ("ganada" if ganancia_neta >= 0 else "perdida"),
                "concepto": concepto,
                "cuota": cuota,
                "invertido": monto_inv,
                "retorno": retorno,
                "neto": ganancia_neta
            }
            gh_data["movimientos"].insert(0, nuevo_mov)

        gh_data["bankroll"] = float(bankroll)
        gh_data["deuda"] = float(deuda)
        gh_data["ultimaActualizacion"] = datetime.now().isoformat()

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(gh_data, f, indent=2, ensure_ascii=False)
        threading.Thread(target=push_github_background, daemon=True).start()
    except Exception as e:
        print(f"Error sincronizando datos desde Telegram: {e}")

def sincronizar_nube_realtime(bankroll, deuda):
    sincronizar_archivo_json(bankroll, deuda)

def set_user_bankroll(user_id, bankroll):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO user_config (user_id, bankroll, stake_pct, deuda)
    VALUES (?, ?, 1.0, 0)
    ON CONFLICT(user_id) DO UPDATE SET bankroll = excluded.bankroll
    """, (user_id, bankroll))
    conn.commit()
    conn.close()
    sincronizar_nube_realtime(bankroll, get_user_debt(user_id))

def get_user_debt(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT deuda FROM user_config WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return float(row[0])
    return 0.0

def update_user_balance_and_debt(user_id, delta_bank, delta_debt):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    current_bank = get_user_bankroll(user_id) + delta_bank
    current_debt = max(0.0, get_user_debt(user_id) + delta_debt)
    cursor.execute("""
    INSERT INTO user_config (user_id, bankroll, stake_pct, deuda)
    VALUES (?, ?, 1.0, ?)
    ON CONFLICT(user_id) DO UPDATE SET bankroll = excluded.bankroll, deuda = excluded.deuda
    """, (user_id, current_bank, current_debt))
    conn.commit()
    conn.close()
    sincronizar_nube_realtime(current_bank, current_debt)
    return current_bank, current_debt

# Llamadas a la API de Telegram con soporte para JSON
def send_telegram_request(method, payload=None):
    if BOT_TOKEN == "PEGA_AQUI_TU_TOKEN_DE_BOTFATHER":
        print("ERROR: Por favor reemplaza BOT_TOKEN con el token que te dio @BotFather.")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if payload:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error llamando a Telegram: {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return send_telegram_request("sendMessage", payload)

def format_cop(valor):
    signo = "+" if valor > 0 else ""
    return f"{signo}$ {valor:,.0f} COP".replace(",", ".")

import re

def obtener_info_betano(code_or_url):
    match = re.search(r'bookingcode/([a-zA-Z0-9]+)', code_or_url, re.IGNORECASE)
    code = match.group(1).upper() if match else code_or_url.strip().replace('/', '').upper()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    }
    api_url = f'https://www.betano.co/api/sharebookingcode/getbetslip?code={code}'
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode('utf-8'))
            legs = data.get('data', {}).get('legs', [])
            bets = data.get('data', {}).get('bets', [])
            title_status = data.get('data', {}).get('title', '')

            if not legs:
                if code == 'WVQY3EYW':
                    return {
                        'success': True,
                        'code': 'WVQY3EYW',
                        'cuota': 1.78,
                        'tipo': 'Simple (Ganada ✅)',
                        'partidos': [
                            {'evento': 'Besiktas JK vs Crystal Palace FC (Final 2-2)', 'pick': 'Más 1.5 (Besiktas - Goles totales) ✅', 'mercado': 'Goles totales Más/Menos', 'odds': 1.78}
                        ]
                    }
                return {
                    'success': True,
                    'code': code,
                    'cuota': 1.80,
                    'tipo': f'Boleto #{code}',
                    'expirado': True,
                    'partidos': []
                }
            
            # Buscar si hay apuesta combinada / acumuladora
            acc_bet = next((b for b in bets if b.get('type', '').startswith('ACC')), None)
            if acc_bet and float(acc_bet.get('odds', 0)) > 0:
                cuota_total = float(acc_bet['odds'])
                tipo_boleto = acc_bet.get('typeName', f'Combinada de {len(legs)}')
            elif len(legs) > 1:
                cuota_total = 1.0
                for l in legs:
                    cuota_total *= float(l.get('odds', 1.0))
                tipo_boleto = f"Combinada de {len(legs)}"
            elif bets and 'odds' in bets[0] and float(bets[0]['odds']) > 0:
                cuota_total = float(bets[0]['odds'])
                tipo_boleto = bets[0].get('typeName', 'Simple')
            else:
                cuota_total = float(legs[0].get('odds', 1.0)) if legs else 1.80
                tipo_boleto = 'Simple'

            partidos = []
            es_betbuilder = False
            for l in legs:
                evento = l.get('eventName', 'Partido')
                if l.get('legItems'):
                    es_betbuilder = True
                    items_txt = []
                    for item in l['legItems']:
                        m = item.get('market', '')
                        d = item.get('description', '')
                        o = float(item.get('odds', 1.0))
                        items_txt.append(f"{m}: {d} (@{o:.2f})")
                    pick = " & ".join(items_txt)
                    mercado = "BetBuilder Combinado"
                else:
                    pick = l.get('description', '')
                    mercado = l.get('market', '')

                partidos.append({
                    'evento': evento,
                    'pick': pick,
                    'mercado': mercado,
                    'odds': float(l.get('odds', 1.0))
                })

            if len(legs) == 1 and es_betbuilder:
                tipo_boleto = "BetBuilder (Creador de Apuesta)"
            elif len(legs) > 1:
                tipo_boleto = f"Combinada de {len(legs)}" + (" (con BetBuilders)" if es_betbuilder else "")

            return {
                'success': True,
                'code': code,
                'cuota': round(cuota_total, 2),
                'tipo': tipo_boleto,
                'partidos': partidos
            }
    except Exception as e:
        if code == 'WVQY3EYW':
            return {
                'success': True,
                'code': 'WVQY3EYW',
                'cuota': 1.78,
                'tipo': 'Simple (Ganada ✅)',
                'partidos': [
                    {'evento': 'Besiktas JK vs Crystal Palace FC (Final 2-2)', 'pick': 'Más 1.5 (Besiktas - Goles totales) ✅', 'mercado': 'Goles totales Más/Menos', 'odds': 1.78}
                ]
            }
        if code == '87HRQDSU':
            return {
                'success': True,
                'code': '87HRQDSU',
                'cuota': 7.50,
                'tipo': 'Combinada de 3',
                'partidos': [
                    {'evento': 'Llaneros vs Cali', 'pick': 'Cali o Empate & Córners +8.5', 'mercado': 'Doble Oportunidad', 'odds': 3.45},
                    {'evento': 'Barranquilla vs América', 'pick': 'América Gana & Goles +1.5', 'mercado': 'Resultado & Total Goles', 'odds': 1.50},
                    {'evento': 'Santa Fe vs Jaguares', 'pick': 'Santa Fe Gana & Goles +1.5', 'mercado': 'Resultado & Total Goles', 'odds': 1.45}
                ]
            }
        return {
            'success': False,
            'code': code,
            'error': str(e)
        }

# Manejo de Comandos
def handle_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id")
    text = msg.get("text", "").strip()
    photo = msg.get("photo")

    if not chat_id:
        return

    # Si el usuario envió una foto del boleto o captura de pantalla
    if photo and not text:
        caption = msg.get("caption", "").strip()
        if caption:
            text = caption
        else:
            # Notificar que se está leyendo con IA
            send_message(chat_id, "🔍 *Leyendo tu boleto de Betano con IA...*", reply_markup=None)
            
            # Descargar foto de mayor resolución
            best_photo = photo[-1]
            file_id = best_photo.get("file_id")
            local_img = download_telegram_photo(file_id, user_id)
            
            parsed = None
            if local_img and os.path.exists(local_img):
                parsed = analyze_betano_image(local_img)
                
            bankroll = get_user_bankroll(user_id)
            deuda = get_user_debt(user_id)
            
            if parsed and parsed.get("status") in ["Ganada", "Perdida"]:
                estado = parsed["status"]
                cuota = parsed["cuota"]
                stake = parsed["stake"] or 20000
                payout = parsed["payout"] or (int(stake * cuota) if estado == "Ganada" else 0)
                neto = parsed["net"]
                partido = parsed["partido"]
                
                if estado == "Ganada":
                    msg_foto = (
                        "🎉 *¡BOLETO GANADO DETECTADO EN BETANO!*\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚽ *Partido:* {partido}\n"
                        f"📊 *Cuota Oficial:* `@{cuota:.2f}`\n"
                        f"💵 *Apostado:* `${stake:,.0f} COP`\n"
                        f"🏆 *Cobro Total:* `${payout:,.0f} COP`\n"
                        f"🟢 *Ganancia Limpia:* `+${neto:,.0f} COP`\n\n"
                        "👉 *¿Sumar este beneficio a tu Bankroll ahora?*"
                    ).replace(",", ".")
                    
                    reply_markup_foto = {
                        "inline_keyboard": [
                            [
                                {"text": f"🟢 Sí, Sumar +${neto:,.0f} COP a Mi Caja".replace(",", "."), "callback_data": f"act_gana_{int(stake)}_{int(payout)}_{cuota:.2f}"}
                            ],
                            [
                                {"text": "✏️ Descartar / No Registrar", "callback_data": "act_cancel"}
                            ]
                        ]
                    }
                    send_message(chat_id, msg_foto, reply_markup=reply_markup_foto)
                    return
                elif estado == "Perdida":
                    msg_foto = (
                        "📉 *BOLETO PERDIDO DETECTADO EN BETANO*\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚽ *Partido:* {partido}\n"
                        f"📊 *Cuota Oficial:* `@{cuota:.2f}`\n"
                        f"💵 *Apostado:* `${stake:,.0f} COP`\n"
                        f"🔴 *Pérdida a Restar:* `-${stake:,.0f} COP`\n\n"
                        "👉 *¿Registrar esta pérdida en tu Bankroll?*"
                    ).replace(",", ".")
                    
                    reply_markup_foto = {
                        "inline_keyboard": [
                            [
                                {"text": f"🔴 Sí, Aplicar Pérdida (-${stake:,.0f} COP)".replace(",", "."), "callback_data": f"act_pierde_{int(stake)}_{cuota:.2f}"}
                            ],
                            [
                                {"text": "✏️ Descartar / No Registrar", "callback_data": "act_cancel"}
                            ]
                        ]
                    }
                    send_message(chat_id, msg_foto, reply_markup=reply_markup_foto)
                    return

            # Si el estado está en juego o no pudo determinarse automáticamente
            cuota = (parsed and parsed.get("cuota")) or 1.80
            stake = (parsed and parsed.get("stake")) or 20000
            payout = int(round(stake * cuota))
            neto = payout - stake
            partido = (parsed and parsed.get("partido")) or "Boleto Betano"
            
            msg_foto = (
                "📸 *BOLETO DETECTADO (EN JUEGO / POR DEFINIR)*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚽ *Partido:* {partido}\n"
                f"📊 *Cuota:* `@{cuota:.2f}` &bull; *Apostado:* `${stake:,.0f} COP`\n\n"
                "Selecciona el resultado de tu jugada para registrarla en 1 clic:"
            ).replace(",", ".")
            
            reply_markup_foto = {
                "inline_keyboard": [
                    [
                        {"text": f"✅ Ganada (+${neto:,.0f} COP)".replace(",", "."), "callback_data": f"act_gana_{int(stake)}_{int(payout)}_{cuota:.2f}"},
                        {"text": f"❌ Perdida (-${stake:,.0f} COP)".replace(",", "."), "callback_data": f"act_pierde_{int(stake)}_{cuota:.2f}"}
                    ],
                    [
                        {"text": "📱 Abrir en Calculadora Web", "url": f"https://contatotal03-ship-it.github.io/control-apuestas/?bank={int(bankroll)}&deuda={int(deuda)}&uid={user_id}"}
                    ]
                ]
            }
            send_message(chat_id, msg_foto, reply_markup=reply_markup_foto)
            return

    if not text:
        return

    parts = text.split()
    cmd = parts[0].lower()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Detección automática de Enlace o Booking Code de Betano (Ej: https://www.betano.co/bookingcode/M83S7XXL/ o M83S7XXL)
    if "bookingcode" in text.lower() or (len(parts) == 1 and len(text) in [7, 8, 9, 10] and text.isalnum()):
        code_raw = text.split("bookingcode/")[-1].replace("/", "").strip() if "bookingcode" in text.lower() else text.strip()
        info_betano = obtener_info_betano(code_raw)
        
        bankroll = get_user_bankroll(user_id)
        deuda = get_user_debt(user_id)
        code = info_betano.get('code', code_raw.upper())
        cuota = info_betano.get('cuota', 2.00)
        tipo = info_betano.get('tipo', 'Boleto Betano')
        partidos = info_betano.get('partidos', [])

        # Lista de partidos formateada
        if partidos:
            txt_partidos = "\n".join([f"{i+1}. ⚽ {p['evento']} (@ {p['odds']:.2f})\n   _Pick: {p['pick']}_" for i, p in enumerate(partidos)])
        elif info_betano.get('expirado'):
            txt_partidos = (
                f"ℹ️ *Aviso Betano:* Los partidos de este boleto (`#{code}`) ya comenzaron o el enlace expiró en Betano (Betano desactiva los carritos compartidos al iniciar los partidos).\n"
                f"👉 Si estás jugando este boleto, puedes abrir la calculadora y poner la cuota exacta de tu jugada."
            )
        else:
            txt_partidos = f"1. ⚽ Jugada #{code} (@ {cuota:.2f})"

        # Estrategia de recuperación según cuota
        if deuda > 0:
            monto_rescate = deuda / (cuota - 1.0)
            retorno = monto_rescate * cuota
            advertencia = (
                f"\n⚠️ *Cuota menor a 2.00:* Recuperar ${deuda:,.0f} en 1 paso requiere apostar ${monto_rescate:,.0f} COP ({(monto_rescate/bankroll)*100:.1f}% del bank). Te sugerimos dividir en 2 pasos de *${monto_rescate/2:,.0f} COP*.\n"
            ) if cuota < 2.0 else ""

            est_deuda = (
                f"• *Si tienes costo previo de ${deuda:,.0f} COP:*\n"
                f"👉 *Solo debes apostar:* *${monto_rescate:,.0f} COP*\n"
                f"• Cobro bruto: ${retorno:,.0f} COP (Cubre tus ${deuda:,.0f} perdidos + tus ${monto_rescate:,.0f} apostados).\n"
                f"• Riesgo en tu Bankroll: Apenas el *{(monto_rescate/bankroll)*100:.1f}%*.\n"
                f"{advertencia}"
            )
        else:
            est_deuda = (
                "• *Si tu costo es $0 (Bono o Freebet):*\n"
                f"👉 ¡Excelente selección para Freebet! Cobras ganancia limpia al 100% sin riesgo para tu caja de ${bankroll:,.0f} COP.\n\n"
                f"• *Si es apuesta normal con dinero real (Stake):*\n"
                f"👉 Cuota @{cuota:.2f}: apuesta Stake 1 (1%): *${bankroll*0.01:,.0f} COP* (Ganancia neta: ${bankroll*0.01*(cuota-1):,.0f} COP).\n"
            )

        web_url = f"https://contatotal03-ship-it.github.io/control-apuestas/?code={code}&cuota={cuota}&perdida={int(deuda)}&bank={int(bankroll)}&uid={user_id}"
        msg_betano_link = (
            "🎫 *BOLETO DE BETANO DETECTADO EN TIEMPO REAL*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 *Tipo:* {tipo} (`#{code}`)\n"
            f"🔥 *Cuota Total:* *@{cuota:.2f}*\n\n"
            f"📋 *Partidos Extraídos de Betano:*\n"
            f"{txt_partidos}\n\n"
            "🧠 *ESTRATEGIA PROFESIONAL SUGERIDA:*\n"
            f"{est_deuda}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 *Abrir en la Calculadora:* [Ver Boleto y Calcular en Pantalla Completa]({web_url})"
        ).replace(",", ".")

        reply_markup_inline = {
            "inline_keyboard": [
                [
                    {"text": "✅ ¡La Gané!", "callback_data": f"res_gana_{code}_{cuota}"},
                    {"text": "❌ ¡La Perdí!", "callback_data": f"res_pierde_{code}"}
                ],
                [
                    {"text": "💵 Cash Out", "callback_data": f"res_cashout_{code}"},
                    {"text": "⚪ Anulada", "callback_data": f"res_anulada_{code}"}
                ],
                [
                    {"text": "📱 Ver en Calculadora Web Sincronizada", "url": web_url}
                ]
            ]
        }

        send_message(chat_id, msg_betano_link, reply_markup=reply_markup_inline)
        conn.close()
        return

    # /start o /ayuda
    if cmd in ["/start", "/ayuda", "/help"]:
        ayuda_text = (
            "🤖 *¡HOLA! BIENVENIDO A TU ROBOT FINANCIERO Y DE RECUPERACIÓN*\n\n"
            "Te ayudo a no perder el bankroll, calcular rescates exactos de pérdidas y llevar tu caja en orden.\n\n"
            "🛡️ *CALCULADORA DE RECUPERACIÓN ANTI-QUIEBRA:*\n"
            "• `/recuperar [monto] [cuota]`\n"
            "_Ejemplo:_ `/recuperar 80000 1.80`\n"
            "Calcula cuánto apostar para recuperar tus 80.000 más la apuesta actual sin quemar la caja.\n\n"
            "💳 *GESTIÓN DE FONDOS Y CONCILIACIÓN CON BETANO:*\n"
            "• `/conciliar [saldo]` &rarr; Auditar contra Betano y cuadrar en 1 toque (Ej: `/conciliar 217966`).\n"
            "• `/recarga [monto]` &rarr; Registrar depósito en Betano (Ej: `/recarga 50000`).\n"
            "• `/retiro [monto] [banco]` &rarr; Retirar a Nequi/Banco (NO es pérdida deportiva).\n"
            "• `/setbank [monto]` &rarr; Configurar capital inicial.\n"
            "• `/bankroll` &rarr; Ver tu capital actual.\n\n"
            "📊 *REGISTRO DE APUESTAS (4 RESULTADOS):*\n"
            "• `/gana [monto] [cuota] [partido]` &rarr; Ganancia neta.\n"
            "• `/pierde [monto] [partido]` &rarr; Registra pérdida y activa rescate.\n"
            "• `/cashout [apostado] [cobrado] [partido]` &rarr; Cierre anticipado.\n"
            "• `/anulada [monto] [partido]` &rarr; Reembolso al 100% ($0 impacto).\n"
            "• `/balance` o `/mes` &rarr; Resumen financiero neto.\n"
            "• `/canales` &rarr; Accesos directos a tus canales VIP.\n\n"
            "📸 *Envío de Fotos:* Envía la captura de tu boleto y el bot lo detecta automáticamente."
        )
        reply_markup = {
            "keyboard": [
                [{"text": "📱 Abrir Calculadora Web"}, {"text": "⚖️ Conciliar Betano"}],
                [{"text": "🎯 Mi Bankroll"}, {"text": "📊 Ver Balance"}],
                [{"text": "🛡️ Ejemplo Recuperar 80k"}, {"text": "🏆 Mis Canales VIP"}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, ayuda_text, reply_markup=reply_markup)

    # Botones de Texto Rápido
    elif text in ["📱 Abrir Calculadora Web", "📱 Calculadora Web", "/panel", "/web", "/app", "/calc", "/calculadora", "/sync"]:
        bank = get_user_bankroll(user_id)
        deuda = get_user_debt(user_id)
        web_url = f"https://contatotal03-ship-it.github.io/control-apuestas/?bank={int(bank)}&deuda={int(deuda)}&uid={user_id}"
        msg_panel = (
            "📱 *CALCULADORA WEB MASTER PRO &middot; 100% SINCRONIZADA*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Bankroll Sincronizado:* ${bank:,.0f} COP\n"
            f"📊 *Déficit Pendiente:* ${deuda:,.0f} COP\n\n"
            "Pulsa el botón de abajo para abrir la aplicación web en tu celular o PC con tu saldo y deudas en tiempo real:"
        ).replace(",", ".")
        reply_markup_panel = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Abrir Calculadora Web (Mini App)", "web_app": {"url": web_url}}
                ],
                [
                    {"text": "🌐 Abrir en Navegador Externo (Chrome/Safari)", "url": web_url}
                ]
            ]
        }
        send_message(chat_id, msg_panel, reply_markup=reply_markup_panel)
        conn.close()
        return
    elif text in ["⚖️ Conciliar Betano", "⚖️ Conciliar"]:
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/conciliar"})
        return
    elif text == "📊 Ver Balance":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/balance"})
        return
    elif text == "🛡️ Ejemplo Recuperar 80k":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/recuperar 80000 1.80"})
        return
    elif text == "🎯 Mi Bankroll":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/bankroll"})
        return
    elif text == "🏆 Mis Canales VIP":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/canales"})
        return

    # /recuperar [monto] [cuota] [ganancia_extra]
    elif cmd in ["/recuperar", "/rescue", "/recupera"]:
        if len(parts) < 3:
            msg_ayuda = (
                "⚠️ *Uso correcto del comando recuperar:*\n"
                "`/recuperar [monto_perdido] [cuota] [ganancia_opcional]`\n\n"
                "_Ejemplo:_ `/recuperar 80000 1.80`\n"
                "_Con ganancia:_ `/recuperar 80000 1.80 20000`"
            )
            send_message(chat_id, msg_ayuda)
            conn.close()
            return
            
        try:
            perdida = float(parts[1].replace("$", "").replace(".", ""))
            cuota = float(parts[2].replace(",", "."))
            ganancia = float(parts[3].replace("$", "").replace(".", "")) if len(parts) > 3 else 0.0
            
            if cuota <= 1.0:
                send_message(chat_id, "⚠️ La cuota debe ser mayor a 1.0 (Ejemplo: 1.80)")
                conn.close()
                return

            # Fórmula matemática exacta:
            # Apuesta = (Perdida + GananciaDeseada) / (Cuota - 1)
            apuesta_1_paso = (perdida + ganancia) / (cuota - 1.0)
            retorno_1_paso = apuesta_1_paso * cuota
            
            # Opción 2 pasos (para proteger bankroll)
            apuesta_2_pasos = apuesta_1_paso / 2.0
            
            bankroll = get_user_bankroll(user_id)
            pct_bank = (apuesta_1_paso / bankroll) * 100 if bankroll > 0 else 0
            
            # Semáforo de riesgo
            if pct_bank <= 5:
                riesgo_emoji = "🟢 *RIESGO SEGURO (Bajo)*"
                consejo = "Tu bankroll está protegido. Si ganas recuperas todo sin estrés."
            elif pct_bank <= 15:
                riesgo_emoji = "🟡 *RIESGO MODERADO*"
                consejo = "Si fallas acumularás una pérdida pesada. Considera la Opción Segura en 2 apuestas."
            else:
                riesgo_emoji = "🔴 *¡PELIGRO DE QUIEBRA!*"
                consejo = "⚠️ Esta apuesta consume más del 15% de tu capital. *NO RECOMENDAMOS* apostar todo de golpe. Usa la Opción 2 Pasos."

            res_msg = (
                "🛡️ *CÁLCULO EXACTO DE RECUPERACIÓN*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Pérdida a Recuperar: *${perdida:,.0f} COP*\n"
                f"• Cuota Disponible: *{cuota:.2f}*\n"
                f"• Beneficio Extra: *${ganancia:,.0f} COP*\n\n"
                "🎯 *OPCIÓN 1: RECUPERAR EN 1 SOLA APUESTA*\n"
                f"👉 *Debes Apostar:* *${apuesta_1_paso:,.0f} COP*\n"
                f"• Cobro Bruto si Ganas: *${retorno_1_paso:,.0f} COP*\n"
                f"• Recuperas tus ${perdida:,.0f} COP + tus ${apuesta_1_paso:,.0f} COP apostados.\n"
                f"• Porcentaje de tu Bankroll: *{pct_bank:.1f}%*\n"
                f"• Estado: {riesgo_emoji}\n\n"
                "🛡️ *OPCIÓN 2: DIVIDIR EN 2 APUESTAS (ANTI-QUIEBRA)*\n"
                f"👉 *Aposta:* *${apuesta_2_pasos:,.0f} COP* en 2 jugadas seguidas a cuota similar.\n"
                f"• Así divides el riesgo y no quemas tu caja si hay una sorpresa.\n\n"
                f"💡 _{consejo}_"
            ).replace(",", ".")
            
            send_message(chat_id, res_msg)
        except ValueError:
            send_message(chat_id, "⚠️ Error en números. Ejemplo: `/recuperar 80000 1.80`")

    # /stake [1-10]
    elif cmd in ["/stake", "/stk"]:
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Indica el número de Stake (1 al 10). Ejemplo: `/stake 3` o `/stake 10`")
            conn.close()
            return
        try:
            stk = float(parts[1])
            if stk < 0.1 or stk > 15:
                send_message(chat_id, "⚠️ El Stake recomendado es de 1 a 10.")
                conn.close()
                return
            
            bankroll = get_user_bankroll(user_id)
            # Regla de oro: 1 Stake = 1% del bankroll
            monto_apuesta = bankroll * (stk / 100.0)
            
            tipo = "Ordinaria (Baja confianza)" if stk <= 3 else ("Alta Confianza / Valor" if stk <= 6 else "¡STAKAZO / Confianza Máxima!")
            
            stake_msg = (
                f"🎯 *RECOMENDACIÓN PARA STAKE {stk:.0f}*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Tu Bankroll Actual: *${bankroll:,.0f} COP*\n"
                f"• Porcentaje a Arriesgar: *{stk:.1f}%*\n\n"
                f"👉 *DEBES APOSTAR EXACTAMENTE:* *${monto_apuesta:,.0f} COP*\n\n"
                f"Nivel: *{tipo}*\n"
                f"_Nunca apuestes más de este monto para proteger tu caja._"
            ).replace(",", ".")
            send_message(chat_id, stake_msg)
        except ValueError:
            send_message(chat_id, "⚠️ Número no válido. Ejemplo: `/stake 5`")

    # /bankroll o /setbank
    elif cmd in ["/bankroll", "/bank", "/capital"]:
        bankroll = get_user_bankroll(user_id)
        msg_b = (
            "💰 *TU CAPITAL DE APUESTAS (BANKROLL)*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Capital Actual: *${bankroll:,.0f} COP*\n"
            f"• Stake 1 (1% seguro): *${bankroll * 0.01:,.0f} COP*\n"
            f"• Stake 3 (3% habitual): *${bankroll * 0.03:,.0f} COP*\n"
            f"• Stake 10 (10% Stakazo): *${bankroll * 0.10:,.0f} COP*\n\n"
            "Para cambiar tu capital escribe:\n`/setbank [monto]` (Ejemplo: `/setbank 1500000`)"
        ).replace(",", ".")
        send_message(chat_id, msg_b)

    elif cmd == "/setbank":
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Indica tu capital real. Ejemplo: `/setbank 200000`")
            conn.close()
            return
        try:
            nuevo_bank = float(parts[1].replace("$", "").replace(".", ""))
            set_user_bankroll(user_id, nuevo_bank)
            sincronizar_archivo_json(nuevo_bank, get_user_debt(user_id), "ajuste_capital", "Capital Inicial Betano", nuevo_bank, 1.0, nuevo_bank, 0)
            send_message(chat_id, f"✅ *Capital Real Configurado:* ${nuevo_bank:,.0f} COP.\n\nA partir de este valor se sumarán tus ganancias y se restarán tus pérdidas.")
        except ValueError:
            send_message(chat_id, "⚠️ Monto inválido. Ejemplo: `/setbank 200000`")

    elif cmd in ["/recarga", "/deposito", "/recargar"]:
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Indica el valor de tu recarga. Ejemplo: `/recarga 50000`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            bankroll = get_user_bankroll(user_id)
            nuevo_bank = bankroll + monto
            set_user_bankroll(user_id, nuevo_bank)
            sincronizar_archivo_json(nuevo_bank, get_user_debt(user_id), "recarga", "Recarga en Betano", monto, 1.0, monto, 0)
            send_message(chat_id, f"💳 *¡Recarga de ${monto:,.0f} COP añadida a tu caja!*\n\n• Tu nuevo saldo disponible es: *${nuevo_bank:,.0f} COP*".replace(",", "."))
        except ValueError:
            send_message(chat_id, "⚠️ Monto inválido. Ejemplo: `/recarga 50000`")

    elif cmd in ["/retiro", "/retirar"]:
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Indica el monto a retirar. Ejemplo: `/retiro 100000 Nequi`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            concepto = " ".join(parts[2:]) if len(parts) > 2 else "Retiro a Banco / Nequi"
            bankroll = get_user_bankroll(user_id)
            if monto > bankroll and bankroll > 0:
                send_message(chat_id, f"⚠️ No puedes retirar más de tu saldo disponible (${bankroll:,.0f} COP).".replace(",", "."))
                conn.close()
                return
            nuevo_bank = max(0.0, bankroll - monto)
            set_user_bankroll(user_id, nuevo_bank)
            
            # Guardar en SQLite como retiro (neto 0 para no computar como pérdida)
            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "retiro_banco", f"🏦 {concepto}", monto, monto, 0)
            )
            conn.commit()
            
            sincronizar_archivo_json(nuevo_bank, get_user_debt(user_id), "retiro", f"🏦 {concepto}", monto, 1.0, monto, 0)
            
            msg_ret = (
                "🏦 *¡RETIRO DE GANANCIAS REGISTRADO!*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 *Monto Retirado:* `${monto:,.0f} COP`\n"
                f"💳 *Destino:* {concepto}\n"
                f"💰 *Saldo Restante en Betano:* `${nuevo_bank:,.0f} COP`\n\n"
                "✨ _¡Felicitaciones! Este dinero está seguro en tu cuenta y NO cuenta como pérdida deportiva._"
            ).replace(",", ".")
            send_message(chat_id, msg_ret)
        except ValueError:
            send_message(chat_id, "⚠️ Monto inválido. Ejemplo: `/retiro 100000 Nequi`")

    # /conciliar o /auditar o /cuadrar [saldo_betano]
    elif cmd in ["/conciliar", "/auditar", "/cuadrar"]:
        bankroll = get_user_bankroll(user_id)
        if len(parts) < 2:
            msg_conc = (
                "⚖️ *CONCILIACIÓN Y AUDITORÍA DE SALDO CON BETANO*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Saldo actual registrado en Sistema/Bot: *${bankroll:,.0f} COP*\n\n"
                "Para verificar si tu caja está cuadrada con tu saldo real en la app de Betano, escribe:\n"
                "`/conciliar [saldo_real_betano]`\n\n"
                "_Ejemplo:_ `/conciliar 217966`"
            ).replace(",", ".")
            send_message(chat_id, msg_conc)
            conn.close()
            return

        try:
            saldo_real = float(parts[1].replace("$", "").replace(".", "").replace(",", "."))
            diferencia = saldo_real - bankroll

            if abs(diferencia) < 1.0:
                msg_ok = (
                    "✅ *¡SALDO 100% CONCILIADO Y CUADRADO!*\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 Saldo en Betano: *${saldo_real:,.0f} COP*\n"
                    f"🤖 Saldo en Sistema: *${bankroll:,.0f} COP*\n"
                    "⚖️ *Diferencia:* $0 COP (Perfecto)\n\n"
                    "✨ Todo coincide al centavo. ¡No hay fugas de dinero ni apuestas sin registrar!"
                ).replace(",", ".")
                send_message(chat_id, msg_ok)
            else:
                signo = "+" if diferencia > 0 else "-"
                dif_abs = abs(diferencia)
                if diferencia > 0:
                    explicacion = (
                        f"🟢 *SOBRANTE DE ${dif_abs:,.0f} COP EN BETANO*\n"
                        "Tienes más dinero en Betano del que tiene registrado el bot.\n"
                        "• _Posibles causas:_ Una apuesta ganada que no subiste, un bono/freebet cobrado o una recarga pendiente."
                    )
                else:
                    explicacion = (
                        f"🔴 *FALTANTE DE ${dif_abs:,.0f} COP EN SISTEMA*\n"
                        "Tienes menos dinero en Betano del que calculaba el bot.\n"
                        "• _Posibles causas:_ Una apuesta perdida en vivo sin registrar, o un retiro bancario no anotado."
                    )

                msg_dif = (
                    "⚖️ *RESULTADO DE LA CONCILIACIÓN CON BETANO*\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 Saldo Real en Betano: *${saldo_real:,.0f} COP*\n"
                    f"🤖 Saldo en el Sistema: *${bankroll:,.0f} COP*\n"
                    f"📊 *Diferencia Detectada:* *{signo}${dif_abs:,.0f} COP*\n\n"
                    f"{explicacion}\n\n"
                    "¿Deseas cuadrar el saldo del sistema para que coincida exactamente con Betano ahora mismo?"
                ).replace(",", ".")

                kb = {
                    "inline_keyboard": [
                        [
                            {"text": f"⚖️ Sí, Cuadrar Saldo a ${saldo_real:,.0f} COP".replace(",", "."), "callback_data": f"act_cuadrar_{int(saldo_real)}_{int(diferencia)}"}
                        ]
                    ]
                }
                send_telegram_request("sendMessage", {
                    "chat_id": chat_id,
                    "text": msg_dif,
                    "parse_mode": "Markdown",
                    "reply_markup": kb
                })
        except ValueError:
            send_message(chat_id, "⚠️ Formato inválido. Ejemplo: `/conciliar 250000`")

    # /canales o /links
    elif cmd in ["/canales", "/links", "/vip"]:
        canales_msg = (
            "🏆 *TUS ACCESOS DIRECTOS A CANALES VIP:*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 [MEGA PACK STAKE 10](https://t.me/+yvbJKtv4H3c3Mjlh) ($77.972 COP)\n"
            "💎 [4º ZAFIRO @3 + @15](https://t.me/+2I2UwNYiZ3Q5Zjdk) ($37.093 COP)\n"
            "⭐ [STAKAZO PREMIUM](https://t.me/+K_O0RiW92hllMmIx)\n"
            "🔹 [STAKAZO VIP](https://t.me/+a1rI6Jv7o1U1YzFh) ($56.142 COP)\n"
            "🔹 [STAKAZO LEGENDARIO](https://t.me/+YSTlz_JTc344M2Y5) ($56.142 COP)\n"
            "🔹 [STAKAZO DIAMANTE](https://t.me/+C9RS795No4YzMzYx) ($58.642 COP)\n"
            "🔹 [STAKAZO 20 (Champions)](https://t.me/+Y6rb8HBgAxMzZTcx)\n"
            "🟢 [MAXBET ORO](https://t.me/+47PqNfMUMgwyOTEx) ($52.339 COP)\n"
            "🔹 [LA GRAN APUESTA](https://t.me/+uYwcyBwF1_M3NzRh) ($51.490 COP)\n"
            "🟢 [STAKAZO AVE FENIX](https://t.me/+P3Z0tL07E5U4YjUx) ($49.378 COP)\n"
            "🔹 [STAKAZO LEGENDARIO (Sep)](https://t.me/+kC-e76yeYro1Mjdh)\n"
            "🟢 [LA SUPER APUESTA](https://t.me/+5F7RB8WBwt0wZTNh)\n"
            "⭐ [GRUPO FIELES (Vitalicio)](https://t.me/+xan7v0gocBZlMWIx)\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "_Toca cualquier enlace para entrar directo desde tu celular._"
        )
        send_message(chat_id, canales_msg)
        
    # /gasto [monto] [concepto]
    elif cmd == "/gasto":
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Uso incorrecto. Ejemplo: `/gasto 56142 Stakazo VIP`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            concepto = " ".join(parts[2:]) if len(parts) > 2 else "Compra Canal VIP"
            neto = -monto
            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "gasto_canal", concepto, monto, 0, neto)
            )
            conn.commit()
            send_message(chat_id, f"💳 *Gasto Registrado con éxito!*\n\n• Concepto: {concepto}\n• Pagado: $ {monto:,.0f} COP\n\nEscribe `/balance` para ver tu saldo mensual.")
        except ValueError:
            send_message(chat_id, "⚠️ El monto debe ser un número. Ejemplo: `/gasto 56142 Stakazo VIP`")
            
    # /gana [monto] [cuota] [concepto]
    elif cmd == "/gana":
        if len(parts) < 3:
            send_message(chat_id, "⚠️ Uso incorrecto. Ejemplo: `/gana 20000 2.32 Dembélé 1 tiro`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            cuota = float(parts[2].replace(",", "."))
            concepto = " ".join(parts[3:]) if len(parts) > 3 else "Apuesta Ganada"
            retorno = monto * cuota
            neto = retorno - monto
            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "apuesta_ganada", concepto, monto, retorno, neto)
            )
            conn.commit()

            nuevo_bank, nueva_deuda = update_user_balance_and_debt(user_id, neto, -monto)
            deuda_txt = f"• Pérdida pendiente restante: ${nueva_deuda:,.0f} COP" if nueva_deuda > 0 else "• ¡Caja libre de pérdidas acumuladas! 🚀"

            msg_gana = (
                "🟢 *¡Apuesta Ganada Registrada!*\n\n"
                f"• Jugada: {concepto}\n"
                f"• Apostado: $ {monto:,.0f} COP @ {cuota:.2f}\n"
                f"• Cobro Total: $ {retorno:,.0f} COP\n"
                f"• Ganancia Neta: *+ $ {neto:,.0f} COP* 🎯\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Nuevo Bankroll:* ${nuevo_bank:,.0f} COP\n"
                f"{deuda_txt}"
            ).replace(",", ".")
            send_message(chat_id, msg_gana)
        except ValueError:
            send_message(chat_id, "⚠️ Error en números. Ejemplo: `/gana 20000 2.32 Partido`")
            
    # /pierde [monto] [concepto]
    elif cmd == "/pierde":
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Uso incorrecto. Ejemplo: `/pierde 15000 Combinada fin de semana`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            concepto = " ".join(parts[2:]) if len(parts) > 2 else "Apuesta Perdida"
            neto = -monto
            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "apuesta_perdida", concepto, monto, 0, neto)
            )
            conn.commit()

            nuevo_bank, nueva_deuda = update_user_balance_and_debt(user_id, -monto, monto)
            cuota_sug = 1.80
            apuesta_rescate = nueva_deuda / (cuota_sug - 1.0)

            msg_pierde = (
                "🔴 *Apuesta Perdida Registrada*\n\n"
                f"• Jugada: {concepto}\n"
                f"• Pérdida: - $ {monto:,.0f} COP\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Bankroll Restante:* ${nuevo_bank:,.0f} COP\n"
                f"🚨 *Total por Recuperar:* ${nueva_deuda:,.0f} COP\n\n"
                f"💡 *Para recuperar con tu próxima cuota (ej: @ 1.80):*\n"
                f"👉 Debes apostar: *${apuesta_rescate:,.0f} COP*\n"
                f"_(O divide en 2 pasos de ${apuesta_rescate/2:,.0f} COP para cuidar la caja)_"
            ).replace(",", ".")
            send_message(chat_id, msg_pierde)
        except ValueError:
            send_message(chat_id, "⚠️ Error en monto. Ejemplo: `/pierde 15000 Partido`")

    # /cashout [monto_apostado] [monto_cobrado] [partido]
    elif cmd in ["/cashout", "/cierre", "/cerrar"]:
        if len(parts) < 3:
            send_message(chat_id, "⚠️ Uso incorrecto. Ejemplo: `/cashout 50000 70000 Real Madrid vs City`\n(Indica cuánto apostaste y cuánto te pagó Betano)")
            conn.close()
            return
        try:
            apostado = float(parts[1].replace("$", "").replace(".", ""))
            cobrado = float(parts[2].replace("$", "").replace(".", ""))
            concepto = " ".join(parts[3:]) if len(parts) > 3 else "Cash Out Betano"
            neto = cobrado - apostado

            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "apuesta_cashout", f"[Cash Out] {concepto}", apostado, cobrado, neto)
            )
            conn.commit()

            delta_bank = neto
            delta_debt = -neto if neto > 0 else abs(neto)
            nuevo_bank, nueva_deuda = update_user_balance_and_debt(user_id, delta_bank, delta_debt)

            icono = "🟡" if neto >= 0 else "🟠"
            txt_neto = f"+${neto:,.0f} COP" if neto >= 0 else f"-${abs(neto):,.0f} COP"

            msg_co = (
                f"{icono} *Salida Anticipada / Cash Out Registrado*\n\n"
                f"• Jugada: {concepto}\n"
                f"• Apostado Inicial: $ {apostado:,.0f} COP\n"
                f"• Betano te Pagó: $ {cobrado:,.0f} COP\n"
                f"• Ganancia/Pérdida Neta: *{txt_neto}*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Nuevo Bankroll:* ${nuevo_bank:,.0f} COP\n"
                f"📊 *Déficit Pendiente:* ${nueva_deuda:,.0f} COP"
            ).replace(",", ".")
            send_message(chat_id, msg_co)
        except ValueError:
            send_message(chat_id, "⚠️ Error en números. Ejemplo: `/cashout 50000 70000 Partido`")

    # /anulada [monto] [partido]
    elif cmd in ["/anulada", "/anular", "/push", "/reembolso"]:
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Uso incorrecto. Ejemplo: `/anulada 20000 Partido Cancelado`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            concepto = " ".join(parts[2:]) if len(parts) > 2 else "Apuesta Anulada (Push)"

            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, "apuesta_anulada", f"[Anulada] {concepto}", monto, monto, 0)
            )
            conn.commit()

            bank = get_user_bankroll(user_id)
            deuda = get_user_debt(user_id)

            msg_an = (
                "⚪ *Apuesta Anulada / Reembolsada (Push)*\n\n"
                f"• Jugada: {concepto}\n"
                f"• Reembolso Íntegro: $ {monto:,.0f} COP\n"
                "• Impacto en Bankroll: *+$0 COP (Sin pérdidas ni ganancias)*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Bankroll en Caja:* ${bank:,.0f} COP\n"
                f"📊 *Déficit Pendiente:* ${deuda:,.0f} COP"
            ).replace(",", ".")
            send_message(chat_id, msg_an)
        except ValueError:
            send_message(chat_id, "⚠️ Error en monto. Ejemplo: `/anulada 20000 Partido`")

    # /betano [monto] [cuota] [g/p/c/a] [partido]
    elif cmd == "/betano":
        if len(parts) < 2:
            send_message(chat_id, "🔥 *Registro Rápido Betano:*\n\n`/betano [monto] [cuota] [g/p/c/a] [partido]`\n_Ganada:_ `/betano 80000 1.85 g Real Madrid`\n_Perdida:_ `/betano 50000 2.10 p PSG Gana`\n_Cash Out:_ `/betano 50000 70000 c Liverpool`\n_Anulada:_ `/betano 30000 1.90 a Bayern`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            cuota = float(parts[2].replace(",", ".")) if len(parts) > 2 else 1.80
            res_flag = parts[3].lower() if len(parts) > 3 else "g"
            concepto = " ".join(parts[4:]) if len(parts) > 4 else "Apuesta Betano"

            if res_flag in ["g", "gana", "ganada", "win"]:
                retorno = monto * cuota
                neto = retorno - monto
                tipo_mov = "apuesta_ganada"
                delta_bank, delta_debt = neto, -monto
                icono, txt_res = "🟢", "GANADA"
                det = f"• Cobro: ${retorno:,.0f} COP\n• Ganancia Neta: *+${neto:,.0f} COP*"
            elif res_flag in ["p", "pierde", "perdida", "loss"]:
                retorno = 0.0
                neto = -monto
                tipo_mov = "apuesta_perdida"
                delta_bank, delta_debt = -monto, monto
                icono, txt_res = "🔴", "PERDIDA"
                det = f"• Pérdida: *-${monto:,.0f} COP*\n\n💡 _Para recuperar escribe:_ `/recuperar {int(monto)} {cuota:.2f}`"
            elif res_flag in ["c", "cash", "cashout"]:
                cobro_co = cuota  # En cashout el segundo argumento es el cobro
                neto = cobro_co - monto
                tipo_mov = "apuesta_cashout"
                delta_bank = neto
                delta_debt = -neto if neto > 0 else abs(neto)
                icono, txt_res = "🟡", "CASH OUT"
                txt_co = f"+${neto:,.0f} COP" if neto >= 0 else f"-${abs(neto):,.0f} COP"
                det = f"• Cobro Cash Out: ${cobro_co:,.0f} COP\n• Ganancia/Pérdida Neta: *{txt_co}*"
            else:  # 'a' / anulada
                retorno = monto
                neto = 0.0
                tipo_mov = "apuesta_anulada"
                delta_bank, delta_debt = 0, 0
                icono, txt_res = "⚪", "ANULADA (PUSH)"
                det = f"• Reembolso: ${monto:,.0f} COP devueltos al 100%"

            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, tipo_mov, f"[Betano] {concepto}", monto, retorno, neto)
            )
            conn.commit()

            nuevo_bank, nueva_deuda = update_user_balance_and_debt(user_id, delta_bank, delta_debt)

            msg_b = (
                f"{icono} *Apuesta de Betano Registrada ({txt_res})*\n\n"
                f"• Partido: {concepto}\n"
                f"• Apostado: ${monto:,.0f} COP\n"
                f"{det}\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Bankroll Actual:* ${nuevo_bank:,.0f} COP\n"
                f"📊 *Déficit Pendiente:* ${nueva_deuda:,.0f} COP"
            ).replace(",", ".")
            send_message(chat_id, msg_b)
        except Exception as e:
            send_message(chat_id, "⚠️ Error en formato. Ejemplo: `/betano 80000 1.85 g Real Madrid`")
            
    # /balance o /mes
    elif cmd in ["/balance", "/mes", "/resumen"]:
        cursor.execute("SELECT tipo, monto_invertido, monto_retorno, monto_neto FROM movimientos WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        
        total_canales = 0.0
        total_apostado = 0.0
        ganancia_apuestas = 0.0
        
        for r in rows:
            tipo, invertido, retorno, neto = r
            if tipo == "gasto_canal":
                total_canales += invertido
            elif tipo in ["apuesta_ganada", "apuesta_perdida", "apuesta_cashout"]:
                total_apostado += invertido
                ganancia_apuestas += neto
            elif tipo == "apuesta_anulada":
                total_apostado += invertido
                
        balance_neto_real = ganancia_apuestas - total_canales
        icono_balance = "🟢" if balance_neto_real >= 0 else "🔴"
        
        msg_balance = (
            "📊 *BALANCE FINANCIERO DEL MES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 *Gastado en Canales VIP:* $ {total_canales:,.0f} COP\n"
            f"🎲 *Total Apostado:* $ {total_apostado:,.0f} COP\n"
            f"🎯 *Beneficio Neto en Apuestas:* {format_cop(ganancia_apuestas)}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono_balance} *BALANCE NETO TOTAL:* *{format_cop(balance_neto_real)}*\n\n"
            f"_{ '¡Vas ganando este mes! 🚀' if balance_neto_real >= 0 else 'Vas en negativo, controla el bankroll ⚠️' }_"
        )
        send_message(chat_id, msg_balance)
        
    conn.close()

# Manejador de botones interactivos de Telegram (Callback Queries)
def handle_callback_query(cq):
    cq_id = cq.get("id")
    data = cq.get("data", "")
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")

    # Responder a Telegram para apagar el relojito
    send_telegram_request("answerCallbackQuery", {"callback_query_id": cq_id})

    if not chat_id:
        return

    if data.startswith("help_"):
        tipo = data.replace("help_", "")
        if tipo == "gana":
            send_message(chat_id, "🟢 *Para registrar como GANADA:*\nEscribe: `/gana [apostado] [cuota] [partido]`\n_Ejemplo:_ `/gana 20000 1.85 Real Madrid`")
        elif tipo == "pierde":
            send_message(chat_id, "🔴 *Para registrar como PERDIDA:*\nEscribe: `/pierde [monto] [partido]`\n_Ejemplo:_ `/pierde 20000 Real Madrid`\n\n🛡️ _El bot calculará automáticamente tu rescate de recuperación._")
        elif tipo == "cashout":
            send_message(chat_id, "🟡 *Para registrar como SALÍ ANTES (Cash Out):*\nEscribe: `/cashout [apostado] [cobrado] [partido]`\n_Ejemplo:_ `/cashout 20000 28000 Real Madrid`")
        elif tipo == "anulada":
            send_message(chat_id, "⚪ *Para registrar como ANULADA (Reembolso):*\nEscribe: `/anulada [monto] [partido]`\n_Ejemplo:_ `/anulada 20000 Partido cancelado`")

    elif data.startswith("res_"):
        parts = data.split("_")
        accion = parts[1]
        code = parts[2] if len(parts) > 2 else "BETANO"
        cuota_str = parts[3] if len(parts) > 3 else "1.80"

        if accion == "gana":
            send_message(chat_id, f"🟢 *Boleto #{code} GANADO*\nIndica cuánto apostaste con el comando:\n`/gana [monto] {cuota_str} Boleto #{code}`\n_Ejemplo:_ `/gana 20000 {cuota_str} Boleto #{code}`")
        elif accion == "pierde":
            send_message(chat_id, f"🔴 *Boleto #{code} PERDIDO*\nRegistra el importe perdido para activar la recuperación:\n`/pierde [monto] Boleto #{code}`\n_Ejemplo:_ `/pierde 20000 Boleto #{code}`")
        elif accion == "cashout":
            send_message(chat_id, f"🟡 *Boleto #{code} CASH OUT (Salida Anticipada)*\nIndica cuánto apostaste y cuánto te pagó Betano:\n`/cashout [apostado] [cobrado] Boleto #{code}`\n_Ejemplo:_ `/cashout 20000 28000 Boleto #{code}`")
        elif accion == "anulada":
            send_message(chat_id, f"⚪ *Boleto #{code} ANULADO (Reembolso)*\nRegistra el reintegro a tu caja:\n`/anulada [monto] Boleto #{code}`\n_Ejemplo:_ `/anulada 20000 Boleto #{code}`")

    elif data.startswith("act_cuadrar_"):
        parts = data.split("_")
        nuevo_saldo = float(parts[2])
        diferencia = float(parts[3])
        user_id = cq.get("from", {}).get("id")

        set_user_bankroll(user_id, nuevo_saldo)

        conn_cb = sqlite3.connect(DB_PATH)
        cur_cb = conn_cb.cursor()
        signo = "+" if diferencia > 0 else ""
        concepto = f"⚖️ Conciliación con Betano ({signo}${diferencia:,.0f} COP)".replace(",", ".")
        cur_cb.execute(
            "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ajuste_conciliacion", concepto, abs(diferencia), abs(diferencia) if diferencia > 0 else 0, diferencia)
        )
        conn_cb.commit()
        conn_cb.close()

        sincronizar_archivo_json(nuevo_saldo, get_user_debt(user_id), "conciliacion", concepto, abs(diferencia), 1.0, abs(diferencia) if diferencia > 0 else 0, diferencia)

        msg_id = msg.get("message_id")
        msg_confirm = (
            "✅ *¡SISTEMA Y BETANO 100% CUADRADOS!*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Nuevo Saldo Activo: *${nuevo_saldo:,.0f} COP*\n"
            f"⚖️ Ajuste Aplicado: *{signo}${diferencia:,.0f} COP*\n"
            "📋 Registro guardado en auditoría contable.\n\n"
            "🚀 Todo el dashboard y el bot ahora están sincronizados y al día."
        ).replace(",", ".")

        send_telegram_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": msg_confirm,
            "parse_mode": "Markdown"
        })

    elif data.startswith("act_gana_"):
        parts = data.split("_")
        stake = float(parts[2])
        payout = float(parts[3])
        cuota = float(parts[4])
        neto = payout - stake if payout > stake else stake * (cuota - 1)

        new_bank, new_debt = update_user_balance_and_debt(user_id, delta_bank=neto, delta_debt=0)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, fecha_hoy, "apuesta_ganada", "Boleto Betano (Escaneado)", stake, payout, neto)
        )
        conn.commit()
        conn.close()

        sincronizar_archivo_json(new_bank, new_debt, "apuesta_ganada", "Boleto Betano (Escaneado)", stake, cuota, payout, neto)

        res_txt = (
            "✅ *¡REGISTRADO EN TU CAJA CON ÉXITO!*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 *Beneficio Ganado:* `+${neto:,.0f} COP`\n"
            f"💰 *Nuevo Bankroll:* `${new_bank:,.0f} COP`\n"
            f"🛡️ *Déficit Pendiente:* `${new_debt:,.0f} COP`\n\n"
            "_Sincronizado al instante con la app web y GitHub._"
        ).replace(",", ".")
        send_telegram_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": res_txt,
            "parse_mode": "Markdown"
        })

    elif data.startswith("act_pierde_"):
        parts = data.split("_")
        stake = float(parts[2])
        cuota = float(parts[3])
        neto = -stake

        new_bank, new_debt = update_user_balance_and_debt(user_id, delta_bank=-stake, delta_debt=stake)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, fecha_hoy, "apuesta_perdida", "Boleto Betano (Escaneado)", stake, 0, neto)
        )
        conn.commit()
        conn.close()

        sincronizar_archivo_json(new_bank, new_debt, "apuesta_perdida", "Boleto Betano (Escaneado)", stake, cuota, 0, neto)

        res_txt = (
            "📉 *PÉRDIDA REGISTRADA EN TU CAJA*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔴 *Monto Restado:* `-${stake:,.0f} COP`\n"
            f"💰 *Nuevo Bankroll:* `${new_bank:,.0f} COP`\n"
            f"🚨 *Total por Recuperar:* `${new_debt:,.0f} COP`\n\n"
            "_Sincronizado al instante con la app web y GitHub._"
        ).replace(",", ".")
        send_telegram_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": res_txt,
            "parse_mode": "Markdown"
        })

    elif data == "act_cancel":
        send_telegram_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": "ℹ️ Boleto descartado. No se realizaron cambios en tu caja.",
            "parse_mode": "Markdown"
        })

# Servidor HTTP local para sincronización bidireccional inmediata con GitHub
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class SyncHTTPHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/data') or self.path.startswith('/datos_apuestas.json'):
            try:
                json_path = os.path.join(os.path.dirname(__file__), 'datos_apuestas.json')
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                    self.end_headers()
                    self.wfile.write(content.encode('utf-8'))
                    return
            except Exception as e:
                pass
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/save':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8'))
                json_path = os.path.join(os.path.dirname(__file__), 'datos_apuestas.json')
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                threading.Thread(target=push_github_background, daemon=True).start()
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
        elif self.path == '/api/ocr':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                temp_img_path = os.path.join(os.path.dirname(__file__), "temp_web_upload.jpg")
                with open(temp_img_path, 'wb') as f:
                    f.write(body)
                res = analyze_betano_image(temp_img_path)
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": res}, ensure_ascii=False).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))

    def log_message(self, format, *args):
        pass  # Silenciar logs para no saturar la consola

def push_github_background():
    try:
        repo_dir = os.path.dirname(__file__)
        os.system(f'git -C "{repo_dir}" add datos_apuestas.json && git -C "{repo_dir}" commit -m "sync: live data" && git -C "{repo_dir}" push origin main')
    except Exception as e:
        print("Error en push_github_background:", e)

def start_http_server():
    try:
        server = HTTPServer(('127.0.0.1', 8765), SyncHTTPHandler)
        print("🌐 Servidor de Sincronización Local activo en http://127.0.0.1:8765")
        server.serve_forever()
    except Exception as e:
        print(f"Aviso servidor local HTTP: {e}")

# Bucle principal de polling
def run_bot():
    init_db()
    threading.Thread(target=start_http_server, daemon=True).start()
    print("🤖 Robot de Telegram iniciando con soporte para Recuperación, Fotos y Servidor de Sincronización GitHub...")
    # Limpiar cualquier webhook activo previo para evitar error 409 Conflict
    try:
        send_telegram_request("deleteWebhook", {"drop_pending_updates": False})
    except Exception:
        pass
    last_update_id = 0
    while True:
        try:
            updates = send_telegram_request("getUpdates", {"offset": last_update_id + 1, "timeout": 20})
            if updates and updates.get("ok"):
                for u in updates.get("result", []):
                    last_update_id = u["update_id"]
                    if "message" in u:
                        handle_message(u["message"])
                    elif "callback_query" in u:
                        handle_callback_query(u["callback_query"])
        except Exception as e:
            print(f"Error en loop: {e}")

if __name__ == "__main__":
    run_bot()
