import pandas as pd
import time
import logging
from binance.client import Client
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET
)
import os
from dotenv import load_dotenv

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Завантаження API ключів з .env
load_dotenv()

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')

# Підключення до Binance API
client = Client(API_KEY, API_SECRET)

# Налаштування
symbol = 'BTCUSDT'  # Трейдимо пару BTC/USDT
interval = Client.KLINE_INTERVAL_5MINUTE  # Таймфрейм 5 хвилин
lookback = '1000'  # Кількість періодів (ліст історичних даних)
ma7_period = 7  # Період для MA7
ma25_period = 25  # Період для MA25
take_profit_percent = 0.10  # Тейк-профіт 10%
stop_loss_percent = 0.02  # Стоп-лосс 2%
trailing_stop_percent = 0.02  # Трейлінг-стоп 2%

# Функція для отримання історичних даних
def get_historical_data(symbol, interval, lookback):
    klines = client.get_historical_klines(symbol, interval, lookback + ' min ago UTC')
    data = []
    for line in klines:
        data.append([line[0], float(line[1]), float(line[2]), float(line[3]), float(line[4]), float(line[5])])
    df = pd.DataFrame(data, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['Time'] = pd.to_datetime(df['Time'], unit='ms')
    return df

# Функція для розрахунку MA
def calculate_ma(df, period):
    return df['Close'].rolling(window=period).mean()

# Функція для розрахунку RSI
def calculate_rsi(df, period=14):
    delta = df['Close'].diff()  # Обчислюємо різницю між цінами закриття
    gain = delta.where(delta > 0, 0)  # Позитивні зміни
    loss = -delta.where(delta < 0, 0)  # Негативні зміни
    
    avg_gain = gain.rolling(window=period, min_periods=1).mean()  # Середнє значення позитивних змін
    avg_loss = loss.rolling(window=period, min_periods=1).mean()  # Середнє значення негативних змін
    
    rs = avg_gain / avg_loss  # Відношення середніх виграшів до середніх збитків
    rsi = 100 - (100 / (1 + rs))  # Формула для RSI
    return rsi

# Функція для отримання балансу
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

# Функція для встановлення стоп-лоссу і трейлінг-стопу
def set_stop_loss_and_trailing_stop(entry_price, action):
    if action == 'buy':
        stop_loss_price = entry_price * (1 - stop_loss_percent)
        trailing_stop_price = entry_price * (1 - trailing_stop_percent)
    else:
        stop_loss_price = entry_price * (1 + stop_loss_percent)
        trailing_stop_price = entry_price * (1 + trailing_stop_percent)
    return stop_loss_price, trailing_stop_price

# Основний цикл бота
def main():
    while True:
        try:
            # Отримуємо історичні дані
            df = get_historical_data(symbol, interval, lookback)
            df['MA7'] = calculate_ma(df, ma7_period)
            df['MA25'] = calculate_ma(df, ma25_period)
            df['RSI'] = calculate_rsi(df)  # Використовуємо функцію для розрахунку RSI

            # Логування останніх даних
            logging.info(f"Latest Data: {df.tail(1)}")

            # Отримуємо баланс
            balance = get_balance()
            logging.info(f"Current USDT balance: {balance}")

            # Перевіряємо перетин MA7 і MA25
            ma7_last = df['MA7'].iloc[-1]
            ma25_last = df['MA25'].iloc[-1]
            ma7_previous = df['MA7'].iloc[-2]
            ma25_previous = df['MA25'].iloc[-2]
            rsi = df['RSI'].iloc[-1]

            position_open = False
            entry_price = 0
            stop_loss_price = 0
            trailing_stop_price = 0

            # Логіка для купівлі та продажу
            if ma7_previous < ma25_previous and ma7_last > ma25_last and rsi > 85 and not position_open:
                logging.info(f"MA7 crossed above MA25, RSI > 85. Considering BUY.")
                if balance > 10:
                    qty = balance // df['Close'].iloc[-1]
                    place_order(symbol, qty, 'BUY')
                    position_open = True
                    entry_price = df['Close'].iloc[-1]
                    stop_loss_price, trailing_stop_price = set_stop_loss_and_trailing_stop(entry_price, 'buy')

            elif ma7_previous > ma25_previous and ma7_last < ma25_last and rsi < 15 and position_open:
                logging.info(f"MA7 crossed below MA25, RSI < 15. Considering SELL.")
                qty = balance // 2  # Продаємо половину балансу
                place_order(symbol, qty, 'SELL')
                position_open = False

            # Затримка перед наступною ітерацією
            time.sleep(60)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
