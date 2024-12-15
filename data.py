import asyncio
import pandas as pd
import numpy as np
import os
import logging
from binance import AsyncClient
from binance.exceptions import BinanceAPIException
import warnings
import pandas_ta as ta  # Для ATR і MACD

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Отримання API ключів з змінних середовища
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

# Підключення до Binance API
client = None
symbol = 'BTCUSDT'

# Функція для отримання історичних даних з Binance для різних таймфреймів
async def get_binance_data(client, interval='1m', limit=1000):
    try:
        klines = await client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        data = pd.DataFrame(klines, columns=[
            'Open time', 'Open', 'High', 'Low', 'Close', 'Volume',
            'Close time', 'Quote asset volume', 'Number of trades',
            'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'
        ])
        data = data[['Open time', 'Open', 'High', 'Low', 'Close', 'Volume']]
        data.rename(columns={'Open time': 'Timestamp'}, inplace=True)
        data['Timestamp'] = pd.to_datetime(data['Timestamp'], unit='ms')
        data[['Open', 'High', 'Low', 'Close', 'Volume']] = data[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
        logger.info(f"Історичні дані ({interval}) завантажено успішно.")
        return data
    except BinanceAPIException as e:
        logger.error(f"Помилка при отриманні даних з Binance: {e}")
        return pd.DataFrame()

# Функція для очищення нових даних (видалення пропусків і непотрібних стовпців)
def clean_data(df):
    # Перевірка на наявність пропусків і заповнення їх попередніми значеннями (forward fill)
    df = df.fillna(method='ffill')

    # Видалення можливих непотрібних стовпців (якщо такі є, перевіряйте назви колонок)
    df = df.loc[:, ~df.columns.str.contains('^Unnamed')]

    # Якщо є додаткові кроки очищення — додайте їх сюди
    return df

# Функція для обчислення індикаторів для кожного таймфрейму
def calculate_indicators(data):
    data['MA7'] = data['Close'].rolling(window=7).mean()
    data['MA25'] = data['Close'].rolling(window=25).mean()

    # ATR (Average True Range) — волатильність ринку
    data['ATR'] = ta.atr(data['High'], data['Low'], data['Close'], length=14)

    # MACD (Moving Average Convergence Divergence)
    macd = ta.macd(data['Close'], fast=12, slow=26, signal=9)
    data['MACD'] = macd['MACD_12_26_9']
    data['MACD_Signal'] = macd['MACDs_12_26_9']
    data['MACD_Histogram'] = macd['MACDh_12_26_9']
    
    # Індекс відносної сили (RSI)
    delta = data['Close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    avg_gain = up.rolling(window=14).mean()
    avg_loss = down.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    data['RSI'] = 100 - (100 / (1 + rs))

    # Смуги Боллінджера (Bollinger Bands)
    data['MiddleBand'] = data['Close'].rolling(window=20).mean()
    data['StdDev'] = data['Close'].rolling(window=20).std()
    data['UpperBand'] = data['MiddleBand'] + (data['StdDev'] * 2)
    data['LowerBand'] = data['MiddleBand'] - (data['StdDev'] * 2)
    
    data.dropna(inplace=True)  # Видалити NaN значення після обчислення індикаторів
    logger.info("Індикатори розраховані і додані до даних.")
    return data

# Основна функція для збору даних і обчислення індикаторів для кількох таймфреймів
async def prepare_data():
    global client
    client = await AsyncClient.create(api_key, api_secret, testnet=True)

    # Визначаємо таймфрейми, для яких будемо отримувати дані
    timeframes = ['1m', '5m', '15m', '30m', '1h']
    all_data = {}

    try:
        # Збираємо дані для кожного таймфрейму
        for tf in timeframes:
            logger.info(f"Завантажуємо дані для таймфрейму {tf}...")
            df = await get_binance_data(client, interval=tf, limit=1000)

            # Очищаємо нові дані
            df = clean_data(df)

            # Обчислюємо індикатори
            df = calculate_indicators(df)

            all_data[tf] = df
            await asyncio.sleep(1)  # щоб уникнути перевищення ліміту запитів API
        
        # Об'єднуємо всі дані в один DataFrame
        all_combined = pd.concat(all_data.values(), keys=all_data.keys())
        
        # Збереження даних в CSV
        all_combined.to_csv('binance_data_with_indicators.csv')
        logger.info("Дані з індикаторами збережені успішно!")

    except Exception as e:
        logger.error(f"Помилка під час збору або обробки даних: {e}")

    finally:
        await client.close_connection()

# Запуск основної функції з використанням правильного циклу подій
if __name__ == "__main__":
    try:
        # Використання SelectorEventLoop для Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(prepare_data())
    except KeyboardInterrupt:
        logger.info("Програма зупинена користувачем.")
    except Exception as e:
        logger.error(f"Несподівана помилка: {e}")
