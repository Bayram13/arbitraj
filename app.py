from flask import Flask, render_template, jsonify
import ccxt
import time
import threading
import requests

app = Flask(__name__)

# === TƏNZİMLƏMƏLƏR ===
ARBITRAGE_PERCENT = 1.0       # Minimum arbitraj fərqi (%)
MAX_ARBITRAGE_PERCENT = 30.0  # Maksimum fərq (saxta tokenləri süzmək üçün)
TELEGRAM_BOT_TOKEN = "SƏNİN_TELEGRAM_BOT_TOKENİNİ_BURAYA_YAZ"
TELEGRAM_CHAT_ID = "SƏNİN_CHAT_İD_BURAYA_YAZ"

# Qlobal yaddaş
live_arbitrage_data = []
active_trades = {}

def send_telegram_message(msg):
    if TELEGRAM_BOT_TOKEN == "SƏNİN_TELEGRAM_BOT_TOKENİNİ_BURAYA_YAZ":
        return 
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram xətası: {e}")

def run_scanner():
    global live_arbitrage_data, active_trades
    
    print("Böyük birjalar quraşdırılır...")
    exchanges = {
        'OKX': ccxt.okx({'options': {'defaultType': 'swap'}}),
        'Binance': ccxt.binance({'options': {'defaultType': 'future'}}),
        'Bybit': ccxt.bybit({'options': {'defaultType': 'linear'}}),
        'MEXC': ccxt.mexc({'options': {'defaultType': 'swap'}}),
        'KuCoin': ccxt.kucoin({'options': {'defaultType': 'swap'}}),
        'GateIO': ccxt.gateio({'options': {'defaultType': 'swap'}}),
        'Bitget': ccxt.bitget({'options': {'defaultType': 'swap'}}),
        'HTX': ccxt.htx({'options': {'defaultType': 'swap'}})
    }
    
    # OKX mərkəzli bütün cütlüklər
    pairs_to_check = [
        ('OKX', 'Binance'), ('OKX', 'Bybit'), ('OKX', 'MEXC'), 
        ('OKX', 'KuCoin'), ('OKX', 'GateIO'), ('OKX', 'Bitget'), ('OKX', 'HTX')
    ]
    
    market_symbols = {}
    all_symbols = set()
    for name, exchange in exchanges.items():
        try:
            exchange.load_markets()
            symbols = set()
            for sym, market in exchange.markets.items():
                if market.get('contract') and market.get('settle') == 'USDT':
                    symbols.add(sym)
            market_symbols[name] = symbols
            all_symbols.update(symbols)
        except Exception as e:
            print(f"{name} yüklənərkən xəta: {e}")
            
    # Yalnız OKX-də olan tokenləri əsas siyahıya alırıq
    common_tokens = [sym for sym in all_symbols if sym in market_symbols.get('OKX', set())]
    print(f"Skan ediləcək ümumi token sayı: {len(common_tokens)}")
    
    while True:
        try:
            all_tickers = {}
            for name, exchange in exchanges.items():
                try:
                    all_tickers[name] = exchange.fetch_tickers()
                except:
                    all_tickers[name] = {}
                    
            # --- ADIM 1: TP YOXLANMASI ---
            tokens_to_remove = []
            for token, trade_info in active_trades.items():
                buy_ex = trade_info['buy_ex']
                sell_ex = trade_info['sell_ex']
                tp_price = trade_info['tp_price']
                
                buy_price = all_tickers.get(buy_ex, {}).get(token, {}).get('last')
                sell_price = all_tickers.get(sell_ex, {}).get(token, {}).get('last')
                
                if buy_price and sell_price:
                    if buy_price >= tp_price or sell_price <= tp_price:
                        tokens_to_remove.append(token)
                        msg = (
                            f"✅ <b>TP VURULDU! ({token})</b>\n\n"
                            f"🎯 <b>Hədəf TP:</b> {tp_price}\n"
                            f"Cütlük: {buy_ex} / {sell_ex}\n"
                        )
                        send_telegram_message(msg)
                        
            for t in tokens_to_remove:
                del active_trades[t]

            # --- ADIM 2: YENİ ARBİTRAJ AXTARIŞI ---
            temp_web_data = []
            
            for token in common_tokens:
                token_arbs = []
                for ex1, ex2 in pairs_to_check:
                    p1 = all_tickers.get(ex1, {}).get(token, {}).get('last')
                    p2 = all_tickers.get(ex2, {}).get(token, {}).get('last')
                    
                    if p1 and p2:
                        if p1 < p2:
                            buy_ex, buy_p, sell_ex, sell_p = ex1, p1, ex2, p2
                        else:
                            buy_ex, buy_p, sell_ex, sell_p = ex2, p2, ex1, p1
                            
                        diff = ((sell_p - buy_p) / buy_p) * 100
                        
                        if ARBITRAGE_PERCENT <= diff <= MAX_ARBITRAGE_PERCENT:
                            token_arbs.append({
                                'token': token,
                                'buy_ex': buy_ex, 'buy_p': buy_p,
                                'sell_ex': sell_ex, 'sell_p': sell_p,
                                'diff': diff,
                                'tp_price': (buy_p + sell_p) / 2
                            })
                            
                if token_arbs:
                    # Fərqi ən böyük olanı seçirik
                    token_arbs.sort(key=lambda x: x['diff'], reverse=True)
                    best_arb = token_arbs[0]
                    temp_web_data.append(best_arb)
                    
                    # Telegrama göndərmək (əgər artıq aktiv deyilsə)
                    if token not in active_trades:
                        active_trades[token] = {
                            'buy_ex': best_arb['buy_ex'],
                            'sell_ex': best_arb['sell_ex'],
                            'tp_price': best_arb['tp_price']
                        }
                        
                        msg = f"⚡ <b>YENİ ARBİTRAJ: {token}</b>\n\n"
                        for arb in token_arbs:
                            msg += (
                                f"🟢 <b>AL:</b> {arb['buy_ex']} ({arb['buy_p']})\n"
                                f"🔴 <b>SAT:</b> {arb['sell_ex']} ({arb['sell_p']})\n"
                                f"📊 <b>Fərq:</b> {round(arb['diff'], 2)}%\n"
                                f"--------------------\n"
                            )
                        msg += f"🎯 <b>Take Profit (Ən yaxşı):</b> {round(best_arb['tp_price'], 4)}\n"
                        msg += f"<i>*Bu token TP olana qədər təkrar göndərilməyəcək.</i>"
                        send_telegram_message(msg)

            # Vebsayt üçün məlumatları yenilə (ən böyük fərqdən kiçiyə doğru)
            temp_web_data.sort(key=lambda x: x['diff'], reverse=True)
            live_arbitrage_data = temp_web_data

        except Exception as e:
            print("Skaner xətası:", e)
            
        time.sleep(5)

# Flask Veb İstiqamətləri
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    return jsonify(live_arbitrage_data)

# Skaneri qlobal işə salırıq ki, Render Gunicorn ilə dərhal başlasın
threading.Thread(target=run_scanner, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
