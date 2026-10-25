import os
import subprocess
import re

def run_windows_ocr(image_path):
    """Ejecuta el OCR nativo de Windows (Windows.Media.Ocr) en el archivo de imagen especificado."""
    if not os.path.exists(image_path):
        return ""
    ps_script_path = os.path.join(os.path.dirname(__file__), "ocr_test.ps1")
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script_path, "-ImagePath", image_path],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20
        )
        return res.stdout.strip()
    except Exception as e:
        print(f"Error ejecutando OCR Windows: {e}")
        return ""

def parse_betano_ticket(text):
    """Interpreta el texto extraído del boleto de Betano y extrae Estado, Cuota, Apostado, Ganancias y Partido."""
    text_clean = text.replace('\r', ' ')
    lower = text.lower()
    
    # 1. Estado oficial del boleto
    status = "en_juego"
    if "ganada" in lower or "acertada" in lower or "ganador" in lower:
        status = "Ganada"
    elif "perdida" in lower or "fallada" in lower:
        status = "Perdida"
    elif "cash out" in lower or "cashout" in lower:
        status = "Cashout"
    elif "anulada" in lower or "cancelada" in lower or "reembolso" in lower:
        status = "Anulada"
        
    # 2. Cuota (Odds)
    odds = 1.80
    # Buscar patrones de cuota como 1.95, 1.78, 2.27, 3.30
    near_match = re.search(r'(?:Ganada|Perdida|Builder)?\s*([1-9]\d{0,1}[.,]\d{2})\b', text, re.IGNORECASE)
    odds_cand = re.findall(r'\b([1-9]\d{0,1}[.,]\d{2})\b', text)
    valid_odds = []
    for m in odds_cand:
        try:
            val = float(m.replace(',', '.'))
            if 1.05 <= val <= 250.0:
                valid_odds.append(val)
        except:
            pass
            
    if near_match:
        try:
            val_near = float(near_match.group(1).replace(',', '.'))
            if 1.05 <= val_near <= 250.0:
                odds = val_near
        except:
            if valid_odds:
                odds = valid_odds[0]
    elif valid_odds:
        odds = valid_odds[0]
        
    # 3. Monto Apostado y Cobro
    stake = 0
    payout = 0
    
    # Simple $50.000 / Doble $ 10.000 / Simple $122.453
    header_stake = re.search(r'(?:Simple|Doble|Triple|Combinada)\s*[\$]?\s*(\d{1,3}(?:\.\d{3})+)', text, re.IGNORECASE)
    if header_stake:
        stake = int(header_stake.group(1).replace('.', ''))
        
    # Ganancias $97.500 / $217.966
    payout_match = re.search(r'(?:Ganancias?|Cobro|Retorno)\s*[\$]?\s*(\d{1,3}(?:\.\d{3})+)', text, re.IGNORECASE)
    if payout_match:
        payout = int(payout_match.group(1).replace('.', ''))
    elif status == "Ganada" and stake > 0:
        payout = int(round(stake * odds))
        
    if not stake:
        all_money = re.findall(r'[\$]\s*(\d{1,3}(?:\.\d{3})+)', text)
        if all_money:
            stake = int(all_money[0].replace('.', ''))
            if len(all_money) > 1 and status == "Ganada" and not payout:
                payout = int(all_money[-1].replace('.', ''))

    # Si fue perdida, el cobro es $0
    if status == "Perdida":
        payout = 0
        net = -stake
    elif status == "Ganada":
        net = (payout - stake) if payout > stake else int(round(stake * (odds - 1)))
    else:
        net = 0

    # 4. Partido / Equipos
    teams = []
    known = [
        "Brisbane Roar", "Perth Glory", "Perth Clory", "Flamengo", "Estudiantes", 
        "Besiktas", "Crystal Palace", "Atletico Nacional", "Deportivo Pasto",
        "Millonarios", "Santa Fe", "Junior", "America", "Cali"
    ]
    for k in known:
        if k.lower() in lower:
            clean_name = "Perth Glory" if "perth" in k.lower() else k
            if clean_name not in teams:
                teams.append(clean_name)
    if len(teams) >= 2:
        partido = f"{teams[0]} vs {teams[1]}"
    elif len(teams) == 1:
        partido = teams[0]
    else:
        partido = "Boleto Betano"

    return {
        "status": status,
        "cuota": odds,
        "stake": stake,
        "payout": payout,
        "net": net,
        "partido": partido,
        "raw_text": text
    }

def analyze_betano_image(image_path):
    text = run_windows_ocr(image_path)
    return parse_betano_ticket(text)

if __name__ == "__main__":
    for p in [
        r"C:\Users\Admin\Downloads\bet.png",
        r"C:\Users\Admin\Downloads\bet (1).png",
        r"C:\Users\Admin\Downloads\bet (2).png",
        r"C:\Users\Admin\Downloads\bet (3).png"
    ]:
        if os.path.exists(p):
            r = analyze_betano_image(p)
            print(f"[{os.path.basename(p)}] Status: {r['status']} | Cuota: {r['cuota']} | Stake: ${r['stake']:,} | Neto: ${r['net']:,} | Partido: {r['partido']}")
