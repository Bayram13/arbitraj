from flask import Flask, render_template, jsonify
import ccxt
import time
import datetime
import threading
import requests

app = Flask(__name__)

# === TƏNZİMLƏMƏLƏR ===
ARBITRAGE_PERCENT = 0.5       # Minimum 0.5% fərq
MAX_ARBITRAGE_PERCENT = 30.0  
MIN_VOLUME_USDT = 100     # Minimum 24 saatlıq həcm (1 Milyon dollar). İstəyə görə dəyişə bilərsiniz.
TELEGRAM_BOT_TOKEN = "SƏNİN_TELEGRAM_BOT_TOKENİNİ_BURAYA_YAZ"
TELEGRAM_CHAT_ID = "SƏNİN_CHAT_İD_BURAYA_YAZ"

live_arbitrage_data = []
active_trades = {}

def get_exchange_url(exchange, token):
    """Token adına uyğun olaraq birjanın birbaşa ticarət linkini yaradır"""
    base_coin = token.split('/')[0].split(':')[0] if '/' in token or ':' in token else token.replace('USDT', '')
    
    urls = {
        'OKX': f"https://www.okx.com/trade-swap/{base_coin}-USDT-SWAP",
        'Binance': f"https://www.binance.com/en/futures/{base_coin}USDT",
        'Bybit': f"https://www.bybit.com/trade/usdt/{base_coin}USDT",
        'MEXC': f"https://futures.mexc.com/exchange/{base_coin}_USDT",
        'KuCoin': f"https://www.kucoin.com/trade/ext/{base_coin}USDTM",
        'GateIO': f"https://www.gate.io/futures_trade/USDT/{base_coin}_USDT",
        'Bitget': f"https://www.bitget.com/mix/usdt/{base_coin}USDT",
        'HTX': f"https://www.htx.com/en-us/contract/linear/{base_coin}-USDT"
    }
    return urls.get(exchange, "#")

def send_telegram_message(msg):
    if TELEGRAM_BOT_TOKEN == "SƏNİN_TELEGRAM_BOT_TOKENİNİ_BURAYA_YAZ":
        return 
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram xətası: {e}")

def get_volume(ticker, price):
    """Bəzi birjalar 'quoteVolume' vermirsə, onu 'baseVolume' * qiymət ilə hesablayır"""
    vol = ticker.get('quoteVolume')
    if vol is not None: 
        return float(vol)
    base_vol = ticker.get('baseVolume')
    if base_vol is not None and price is not None: 
        return float(base_vol) * float(price)
    return 0.0

def run_scanner():
    global live_arbitrage_data, active_trades
    
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
            pass
            
    common_tokens = [sym for sym in all_symbols if sym in market_symbols.get('OKX', set())]
    
    while True:
        try:
            all_tickers = {name: ex.fetch_tickers() for name, ex in exchanges.items() if ex.has['fetchTickers']}
            current_time = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            
            # --- ADIM 1: TP YOXLANMASI ---
            tokens_to_remove = []
            for token, trade_info in active_trades.items():
                buy_price = all_tickers.get(trade_info['buy_ex'], {}).get(token, {}).get('last')
                sell_price = all_tickers.get(trade_info['sell_ex'], {}).get(token, {}).get('last')
                tp_price = trade_info['tp_price']
                
                if buy_price and sell_price:
                    if buy_price >= tp_price or sell_price <= tp_price:
                        tokens_to_remove.append(token)
                        msg = (
                            f"✅ <b>TP HIT! ({token})</b>\n\n"
                            f"🎯 <b>Target TP:</b> {tp_price}\n"
                            f"Pair: {trade_info['buy_ex']} / {trade_info['sell_ex']}\n"
                            f"🕒 <b>Time:</b> {current_time}"
                        )
                        send_telegram_message(msg)
                        
            for t in tokens_to_remove:
                del active_trades[t]

            # --- ADIM 2: YENİ ARBİTRAJ AXTARIŞI ---
            temp_web_data = []
            for token in common_tokens:
                token_arbs = []
                for ex1, ex2 in pairs_to_check:
                    ticker1 = all_tickers.get(ex1, {}).get(token, {})
                    ticker2 = all_tickers.get(ex2, {}).get(token, {})
                    
                    p1 = ticker1.get('last')
                    p2 = ticker2.get('last')
                    
                    if p1 and p2:
                        # Həcm qoruması (Volume Protection) yoxlanışı
                        vol1 = get_volume(ticker1, p1)
                        vol2 = get_volume(ticker2, p2)
                        
                        # Əgər hər iki birjada həcm limitdən böyükdürsə
                        if vol1 >= MIN_VOLUME_USDT and vol2 >= MIN_VOLUME_USDT:
                            buy_ex, buy_p, sell_ex, sell_p = (ex1, p1, ex2, p2) if p1 < p2 else (ex2, p2, ex1, p1)
                            diff = ((sell_p - buy_p) / buy_p) * 100
                            
                            if ARBITRAGE_PERCENT <= diff <= MAX_ARBITRAGE_PERCENT:
                                token_arbs.append({
                                    'token': token,
                                    'buy_ex': buy_ex, 'buy_p': buy_p, 'buy_url': get_exchange_url(buy_ex, token),
                                    'sell_ex': sell_ex, 'sell_p': sell_p, 'sell_url': get_exchange_url(sell_ex, token),
                                    'diff': diff,
                                    'tp_price': (buy_p + sell_p) / 2,
                                    'time': current_time
                                })
                            
                if token_arbs:
                    token_arbs.sort(key=lambda x: x['diff'], reverse=True)
                    best_arb = token_arbs[0]
                    temp_web_data.append(best_arb)
                    
                    if token not in active_trades:
                        active_trades[token] = {
                            'buy_ex': best_arb['buy_ex'], 'sell_ex': best_arb['sell_ex'], 'tp_price': best_arb['tp_price']
                        }
                        msg = f"⚡ <b>NEW ARBITRAGE: {token}</b>\n\n"
                        for arb in token_arbs:
                            msg += (
                                f"🟢 <b>BUY:</b> <a href='{arb['buy_url']}'>{arb['buy_ex']}</a> ({arb['buy_p']})\n"
                                f"🔴 <b>SELL:</b> <a href='{arb['sell_url']}'>{arb['sell_ex']}</a> ({arb['sell_p']})\n"
                                f"📊 <b>Diff:</b> {round(arb['diff'], 2)}%\n"
                                f"--------------------\n"
                            )
                        msg += f"🎯 <b>Take Profit:</b> {round(best_arb['tp_price'], 4)}\n"
                        msg += f"🕒 <b>Time:</b> {current_time}"
                        send_telegram_message(msg)

            temp_web_data.sort(key=lambda x: x['diff'], reverse=True)
            live_arbitrage_data = temp_web_data

        except Exception as e:
            print("Skaner xətası:", e)
        time.sleep(5)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    return jsonify(live_arbitrage_data)

threading.Thread(target=run_scanner, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
