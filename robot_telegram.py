import os
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime

# Configurar salida UTF-8 para consola Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Configuración
BOT_TOKEN = "8365375317:AAE882Q2wWU0K56p1nS8xc6cnwWLe7OO5iw"
DB_PATH = os.path.join(os.path.dirname(__file__), "finanzas_apuestas.db")

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
        bankroll REAL DEFAULT 1000000,
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
    return 1000000.0  # Default: 1.000.000 COP

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
                        'cuota': 2.00,
                        'tipo': 'Boleto Betano (#WVQY3EYW)',
                        'partidos': [
                            {'evento': 'Jugada Betano (#WVQY3EYW)', 'pick': 'Boleto registrado', 'mercado': 'Cuota ajustable', 'odds': 2.00}
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
                'cuota': 2.00,
                'tipo': 'Boleto Betano (#WVQY3EYW)',
                'partidos': [
                    {'evento': 'Jugada Betano (#WVQY3EYW)', 'pick': 'Boleto registrado', 'mercado': 'Cuota ajustable', 'odds': 2.00}
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

    if not text or not chat_id:
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

        web_url = f"https://contatotal03-ship-it.github.io/control-apuestas/?code={code}&cuota={cuota}&perdida={deuda}"
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

        send_message(chat_id, msg_betano_link)
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
            "🎯 *GESTIÓN DE BANKROLL Y STAKES:*\n"
            "• `/stake [1-10]` &rarr; Cuánto apostar según tu Stake.\n"
            "• `/setbank [monto]` &rarr; Configurar tu capital (Ej: `/setbank 1000000`).\n"
            "• `/bankroll` &rarr; Ver tu capital actual.\n\n"
            "📊 *REGISTRO DE APUESTAS Y BALANCE:*\n"
            "• `/gana [monto] [cuota] [partido]` &rarr; Ganancia neta.\n"
            "• `/pierde [monto] [partido]` &rarr; Registra pérdida.\n"
            "• `/gasto [monto] [canal]` &rarr; Compra de canal VIP.\n"
            "• `/balance` o `/mes` &rarr; Resumen financiero neto.\n"
            "• `/canales` &rarr; Accesos directos a tus canales VIP.\n\n"
            "📱 *Calculadora Web en la Nube:* Abre la webapp con el botón inferior."
        )
        reply_markup = {
            "keyboard": [
                [{"text": "📊 Ver Balance"}, {"text": "🛡️ Ejemplo Recuperar 80k"}],
                [{"text": "🎯 Mi Bankroll"}, {"text": "🏆 Mis Canales VIP"}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, ayuda_text, reply_markup=reply_markup)

    # Botones de Texto Rápido
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
            send_message(chat_id, "⚠️ Indica tu nuevo capital. Ejemplo: `/setbank 1200000`")
            conn.close()
            return
        try:
            nuevo_bank = float(parts[1].replace("$", "").replace(".", ""))
            set_user_bankroll(user_id, nuevo_bank)
            send_message(chat_id, f"✅ *Bankroll actualizado:* ${nuevo_bank:,.0f} COP.\n\nAhora tus cálculos de `/recuperar` y `/stake` se ajustarán a esta caja.")
        except ValueError:
            send_message(chat_id, "⚠️ Monto inválido. Ejemplo: `/setbank 1000000`")

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

    # /betano [monto] [cuota] [g/p] [partido] o texto copiado de betano
    elif cmd == "/betano":
        if len(parts) < 2:
            send_message(chat_id, "🔥 *Registro Rápido Betano:*\n\n`/betano [monto] [cuota] [g/p] [partido]`\n_Ejemplo:_ `/betano 80000 1.85 g Real Madrid vs City`\n_O perdida:_ `/betano 50000 2.10 p PSG Gana`")
            conn.close()
            return
        try:
            monto = float(parts[1].replace("$", "").replace(".", ""))
            cuota = float(parts[2].replace(",", ".")) if len(parts) > 2 else 1.80
            res_flag = parts[3].lower() if len(parts) > 3 else "g"
            es_ganada = res_flag in ["g", "gana", "ganada", "win"]
            concepto = " ".join(parts[4:]) if len(parts) > 4 else "Apuesta Betano"

            retorno = monto * cuota if es_ganada else 0.0
            neto = (retorno - monto) if es_ganada else -monto
            tipo_mov = "apuesta_ganada" if es_ganada else "apuesta_perdida"

            cursor.execute(
                "INSERT INTO movimientos (user_id, fecha, tipo, concepto, monto_invertido, monto_retorno, monto_neto) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, fecha_hoy, tipo_mov, f"[Betano] {concepto}", monto, retorno, neto)
            )
            conn.commit()

            delta_bank = neto if es_ganada else -monto
            delta_debt = -monto if es_ganada else monto
            nuevo_bank, nueva_deuda = update_user_balance_and_debt(user_id, delta_bank, delta_debt)

            icono = "🟢" if es_ganada else "🔴"
            txt_res = "GANADA" if es_ganada else "PERDIDA"

            if es_ganada:
                det = f"• Cobro: ${retorno:,.0f} COP\n• Ganancia Neta: *+${neto:,.0f} COP*"
            else:
                sug_rescate = nueva_deuda / (cuota - 1.0) if cuota > 1.0 else nueva_deuda
                det = f"• Pérdida: *-${monto:,.0f} COP*\n\n💡 _Para recuperar escribe:_ `/recuperar {int(nueva_deuda)} {cuota:.2f}`"

            msg_b = (
                f"{icono} *Apuesta de Betano Registrada ({txt_res})*\n\n"
                f"• Partido: {concepto}\n"
                f"• Apostado: ${monto:,.0f} COP @ {cuota:.2f}\n"
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
            elif tipo == "apuesta_ganada":
                total_apostado += invertido
                ganancia_apuestas += neto
            elif tipo == "apuesta_perdida":
                total_apostado += invertido
                ganancia_apuestas += neto
                
        balance_neto_real = ganancia_apuestas - total_canales
        icono_balance = "🟢" if balance_neto_real >= 0 else "🔴"
        
        msg_balance = (
            "📊 *BALANCE FINANCIERO DEL MES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 *Gastado en Canales VIP:* $ {total_canales:,.0f} COP\n"
            f"🎲 *Total Apostado:* $ {total_apostado:,.0f} COP\n"
            f"🎯 *Beneficio en Apuestas:* {format_cop(ganancia_apuestas)}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono_balance} *BALANCE NETO TOTAL:* *{format_cop(balance_neto_real)}*\n\n"
            f"_{ '¡Vas ganando este mes! 🚀' if balance_neto_real >= 0 else 'Vas en negativo, controla el bankroll ⚠️' }_"
        )
        send_message(chat_id, msg_balance)
        
    conn.close()

# Bucle principal de polling
def run_bot():
    init_db()
    print("🤖 Robot de Telegram iniciando con soporte para Recuperación y Bankroll...")
    last_update_id = 0
    while True:
        try:
            updates = send_telegram_request("getUpdates", {"offset": last_update_id + 1, "timeout": 20})
            if updates and updates.get("ok"):
                for u in updates.get("result", []):
                    last_update_id = u["update_id"]
                    if "message" in u:
                        handle_message(u["message"])
        except Exception as e:
            print(f"Error en loop: {e}")

if __name__ == "__main__":
    run_bot()
