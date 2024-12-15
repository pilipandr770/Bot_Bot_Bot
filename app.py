import pandas as pd
import time
import logging
from binance.client import Client
from binance.enums import (
    SIDE_SELL,
    ORDER_TYPE_MARKET,
    # Видаляємо ORDER_TYPE_STOP_MARKET і ORDER_TYPE_TRAILING_STOP_MARKET
)
# ... existing code ...

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')

# Підключення до Binance API
client = Client(API_KEY, API_SECRET)

# Налаштування
symbol = 'BTCUSDT'  # Трейдимо пару BTC/USDT
interval = Client.KLINE_INTERVAL_5MINUTE  # Таймфрейм 5 хвилин
lookback = '1000'  # Кількість періодів (ліст історичних даних)
rsi_period = 14  # Період для розрахунку RSI
rsi_buy_threshold = 30  # Поріг для купівлі (RSI 30)
rsi_sell_threshold = 70  # Поріг для продажу (RSI 70)

# Функція для отримання історичних даних
def get_historical_data(symbol, interval, lookback):
    klines = client.get_historical_klines(symbol, interval, lookback + ' min ago UTC')
    data = []
    for line in klines:
        data.append([line[0], float(line[1]), float(line[2]), float(line[3]), float(line[4]), float(line[5])])
    df = pd.DataFrame(data, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['Time'] = pd.to_datetime(df['Time'], unit='ms')
    return df

# Функція для розрахунку індикатора RSI
def calculate_rsi(df, period=rsi_period):
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# Функція для отримання поточного балансу
def get_balance():
    balance = client.get_asset_balance(asset='USDT')
    return float(balance['free'])

# Функція для відкриття ордера
def place_order(symbol, quantity, side, order_type=ORDER_TYPE_MARKET):
    try:
        if side == 'BUY':
            logging.info(f"Buying {quantity} {symbol}")
            order = client.order_market_buy(symbol=symbol, quantity=quantity)
        elif side == 'SELL':
            logging.info(f"Selling {quantity} {symbol}")
            order = client.order_market_sell(symbol=symbol, quantity=quantity)
        logging.info(f"Order executed: {order}")
    except Exception as e:
        logging.error(f"Error executing order: {e}")

# Функція для виставлення стоп-лосс та трейлінг стоп
def set_stop_loss_trailing(symbol, quantity, stop_loss_price, trailing_stop_price):
    try:
        # Використовуємо STOP_LOSS замість STOP_MARKET
        order = client.create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type='STOP_LOSS',
            stopPrice=stop_loss_price,
            quantity=quantity
        )
        logging.info(f"Stop loss set at {stop_loss_price}")
        
        # Використовуємо TRAILING_STOP_LOSS
        order = client.create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type='TRAILING_STOP',
            stopPrice=trailing_stop_price,
            quantity=quantity
        )
        logging.info(f"Trailing stop set at {trailing_stop_price}")
    except Exception as e:
        logging.error(f"Error setting stop loss or trailing stop: {e}")
# Додайте нові функції після налаштувань
def get_symbol_info(symbol):
    return client.get_symbol_info(symbol)

def calculate_quantity(balance, current_price, symbol_info):
    # Отримуємо фільтри для правильного розрахунку кількості
    lot_size_filter = next(filter(lambda x: x['filterType'] == 'LOT_SIZE', symbol_info['filters']))
    price_filter = next(filter(lambda x: x['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    
    min_qty = float(lot_size_filter['minQty'])
    step_size = float(lot_size_filter['stepSize'])
    tick_size = float(price_filter['tickSize'])
    
    # Розраховуємо максимальну можливу кількість
    max_qty = balance / current_price
    
    # Округляємо до правильної кількості знаків після коми
    precision = len(str(step_size).rstrip('0').split('.')[-1])
    quantity = round(max_qty - (max_qty % float(step_size)), precision)
    
    # Перевіряємо чи не менше мінімальної кількості
    if quantity < min_qty:
        return 0
    return quantity

def round_price(price, symbol_info):
    price_filter = next(filter(lambda x: x['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    tick_size = float(price_filter['tickSize'])
    precision = len(str(tick_size).rstrip('0').split('.')[-1])
    return round(price, precision)

# Оновіть основний цикл main():
def main():
    symbol_info = get_symbol_info(symbol)
    
    while True:
        try:
            df = get_historical_data(symbol, interval, lookback)
            df['RSI'] = calculate_rsi(df)
            
            current_price = float(df['Close'].iloc[-1])
            balance = get_balance()
            
            logging.info(f"Latest Data: {df.tail(1)}")
            logging.info(f"Current USDT balance: {balance}")
            
            if df['RSI'].iloc[-1] < rsi_buy_threshold:
                logging.info(f"RSI is below {rsi_buy_threshold}. Considering BUY.")
                if balance > 10:
                    qty = calculate_quantity(balance, current_price, symbol_info)
                    if qty > 0:
                        place_order(symbol, qty, 'BUY')
                        time.sleep(2)
                        
                        # Виставляємо стоп-лосс і трейлінг стоп тільки після успішної покупки
                        stop_loss_price = round_price(current_price * 0.98, symbol_info)
                        trailing_stop_price = round_price(current_price * 0.99, symbol_info)
                        set_stop_loss_trailing(symbol, qty, stop_loss_price, trailing_stop_price)
                    else:
                        logging.info("Insufficient balance for minimum order size")
                        
            elif df['RSI'].iloc[-1] > rsi_sell_threshold:
                logging.info(f"RSI is above {rsi_sell_threshold}. Considering SELL.")
                btc_balance = float(client.get_asset_balance(asset='BTC')['free'])
                if btc_balance > 0:
                    qty = calculate_quantity(btc_balance * current_price, current_price, symbol_info)
                    if qty > 0:
                        place_order(symbol, qty, 'SELL')
                    else:
                        logging.info("Insufficient BTC balance for minimum order size")
            
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(60)# Основний цикл бота


if __name__ == "__main__":
    main()
